from __future__ import annotations

import json
import threading
from pathlib import Path

from mp_agent.infrastructure.artifacts import ARTIFACTS_DIR

CACHE_DIR = ARTIFACTS_DIR / "cache"
_lock = threading.Lock()


def _cache_path(platform: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{platform}_cache.json"


def load_platform_cache(platform: str) -> dict[str, dict]:
    path = _cache_path(platform)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_platform_cache(platform: str, cache: dict[str, dict]) -> None:
    path = _cache_path(platform)
    with _lock:
        with path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cached_entry(platform: str, product_id: str) -> dict | None:
    """Return cached analysis row for product_id, or None if not cached."""
    return load_platform_cache(platform).get(product_id)


def save_cached_entry(platform: str, product_id: str, row: dict) -> None:
    """Persist a completed analysis row into the platform cache."""
    cache = load_platform_cache(platform)
    cache[product_id] = row
    save_platform_cache(platform, cache)
