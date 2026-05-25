"""
Shared FlareSolverr fetch helper with concurrency protection.

Two-layer guard:
  1. Per-session asyncio.Lock  — serialises requests to the same named Chrome
     driver, preventing Selenium race conditions when multiple coroutines share
     a session (e.g. two users both querying Worten simultaneously).
  2. Global asyncio.Semaphore  — caps the total number of Chrome instances
     running at once across all platforms, preventing OOM.

Default concurrency limit: 3 (override with FLARESOLVERR_MAX_CONCURRENT env var).
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable

import httpx

FLARESOLVERR_URL = "http://localhost:8191/v1"
_MAX_CONCURRENT = int(os.getenv("FLARESOLVERR_MAX_CONCURRENT", "3"))

# Lazily initialised so they are created inside the running event loop.
_semaphore: asyncio.Semaphore | None = None
_session_locks: dict[str, asyncio.Lock] = {}


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


def _get_session_lock(session: str) -> asyncio.Lock:
    if session not in _session_locks:
        _session_locks[session] = asyncio.Lock()
    return _session_locks[session]


async def flare_fetch(
    url: str,
    *,
    session: str,
    max_timeout: int = 60_000,
    http_timeout: int = 90,
    min_response_bytes: int = 0,
    retries: int = 2,
    platform: str = "flaresolverr",
    post_process: Callable[[str], str] | None = None,
) -> str:
    """
    Fetch *url* through FlareSolverr with full concurrency protection.

    Args:
        url:               Target URL.
        session:           FlareSolverr named session (one Chrome per name).
        max_timeout:       FlareSolverr-side timeout in ms.
        http_timeout:      httpx client timeout in seconds.
        min_response_bytes: If > 0, responses shorter than this are treated as
                           under-rendered and retried.
        retries:           Extra attempts after the first failure.
        platform:          Used only in log messages.
        post_process:      Optional callable applied to the raw HTML before
                           returning (e.g. Kaufland's unicode-escape fix).
    """
    session_lock = _get_session_lock(session)
    semaphore = _get_semaphore()

    # session_lock first: serialise requests to the same Chrome driver.
    # semaphore second: cap total concurrent Chrome instances.
    async with session_lock:
        async with semaphore:
            return await _do_fetch(
                url,
                session=session,
                max_timeout=max_timeout,
                http_timeout=http_timeout,
                min_response_bytes=min_response_bytes,
                retries=retries,
                platform=platform,
                post_process=post_process,
            )


async def _destroy_session(session: str) -> None:
    """Destroy a named FlareSolverr session so the next request gets a fresh Chrome."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(FLARESOLVERR_URL, json={"cmd": "sessions.destroy", "session": session})
        print(f"[flaresolverr] session '{session}' destroyed — fresh Chrome on next request")
    except Exception as exc:
        print(f"[flaresolverr] failed to destroy session '{session}': {exc}")


async def _do_fetch(
    url: str,
    *,
    session: str,
    max_timeout: int,
    http_timeout: int,
    min_response_bytes: int,
    retries: int,
    platform: str,
    post_process: Callable[[str], str] | None,
) -> str:
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout,
        "session": session,
    }
    last_err: Exception | None = None

    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=http_timeout) as client:
                r = await client.post(FLARESOLVERR_URL, json=payload)
            data = r.json()
            if data.get("status") == "ok":
                html: str = data.get("solution", {}).get("response", "") or ""
                if min_response_bytes and len(html) < min_response_bytes:
                    last_err = RuntimeError(
                        f"under-rendered page ({len(html)} bytes) for {url}"
                    )
                    print(f"[{platform}] under-rendered ({len(html)} bytes), retrying…")
                elif html:
                    if post_process:
                        html = post_process(html)
                    return html
                else:
                    last_err = RuntimeError(f"empty response for {url}")
            else:
                # FlareSolverr returned an error — Chrome may be hung or crashed.
                # Destroy the session so the next attempt starts with a fresh browser.
                last_err = RuntimeError(
                    f"FlareSolverr error: {data.get('message')} (HTTP {r.status_code})"
                )
                await _destroy_session(session)
        except Exception as exc:
            last_err = exc

        if attempt < retries:
            print(f"[{platform}] attempt {attempt + 1} failed ({last_err}), retrying…")
            await asyncio.sleep(5)  # give Chrome time to start up after session destroy

    raise last_err or RuntimeError(f"FlareSolverr failed for {url}")
