from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from mp_agent.application.primary_agent import decide_next_step, summarize_workflow_result
from mp_agent.application.session_store import SessionStore
from mp_agent.application.workflow_registry import build_default_registry
from mp_agent.dao.repository import create_crawl_task, update_crawl_task
from mp_agent.infrastructure.artifacts import write_multi_platform_analysis_csv


SESSION_STORE = SessionStore()
WORKFLOW_REGISTRY = build_default_registry()
RUNS: dict[str, "AgentRun"] = {}


class EventQueue:
    def __init__(self):
        self._items = deque()
        self._waiters = deque()

    async def put(self, payload: dict) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(payload)
                return
        self._items.append(payload)

    async def get(self) -> dict:
        if self._items:
            return self._items.popleft()

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters.append(waiter)
        try:
            return await waiter
        finally:
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass

    def get_nowait(self) -> dict:
        if not self._items:
            raise asyncio.QueueEmpty
        return self._items.popleft()

    def empty(self) -> bool:
        return not self._items


@dataclass
class AgentRun:
    run_id: str
    session_id: str
    queue: EventQueue


def new_session(*, session_store: SessionStore = SESSION_STORE):
    return session_store.create_session()


def get_session_payload(session_id: str, *, session_store: SessionStore = SESSION_STORE) -> dict:
    session = session_store.get_session(session_id)
    return {
        "session_id": session.session_id,
        "messages": [{"role": message.role, "content": message.content} for message in session.messages],
        "slots": {
            "platform": session.slots.platform,
            "brand": session.slots.brand,
            "count": session.slots.count,
        },
        "active_run_id": session.active_run_id,
    }


def new_run(
    session_id: str,
    message: str,
    *,
    session_store: SessionStore = SESSION_STORE,
    runs: dict[str, AgentRun] = RUNS,
    queue_factory=EventQueue,
) -> AgentRun:
    run_id = session_store.start_run(session_id)
    try:
        run = AgentRun(run_id=run_id, session_id=session_id, queue=queue_factory())
        runs[run.run_id] = run
        session_store.append_message(session_id, "user", message)
        return run
    except Exception:
        session_store.finish_run(session_id, run_id)
        runs.pop(run_id, None)
        raise


def discard_run(run_id: str, *, runs: dict[str, AgentRun] = RUNS) -> None:
    runs.pop(run_id, None)


async def emit_event(queue: EventQueue, payload: dict) -> None:
    await queue.put(payload)


def build_artifact_event(result: dict) -> dict:
    return {
        "type": "artifact",
        "artifact_type": "csv_preview",
        "summary": result["summary"],
        "preview_columns": result["preview_columns"],
        "preview_rows": result["preview_rows"],
        "filename": result["filename"],
        "download_url": result["download_url"],
    }


def build_error_event(message: str) -> dict:
    return {
        "type": "error",
        "message": message,
    }


def _fallback_summary_message(result: dict) -> str:
    summary = result.get("summary", "")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return "任务已完成。"


async def run_session_message(
    session_id: str,
    run_id: str,
    queue: EventQueue,
    *,
    session_store: SessionStore = SESSION_STORE,
    workflow_registry=WORKFLOW_REGISTRY,
    decide_next_step_fn=decide_next_step,
    summarize_result_fn=summarize_workflow_result,
    runs: dict[str, AgentRun] = RUNS,
) -> None:
    try:
        try:
            session = session_store.get_session(session_id)
            tool_schemas = workflow_registry.get_tool_schemas() if workflow_registry is not None else []
            decision = decide_next_step_fn(session.messages, session.slots, tool_schemas)

            slot_updates = decision.get("slot_updates", {})
            session_store.update_slots(session_id, **slot_updates)

            if decision["type"] == "assistant":
                session_store.append_message(session_id, "assistant", decision["message"])
                await emit_event(queue, {"type": "assistant", "message": decision["message"]})
                await emit_event(queue, {"type": "done"})
                return

            if decision.get("assistant_message"):
                session_store.append_message(session_id, "assistant", decision["assistant_message"])
                await emit_event(queue, {"type": "assistant", "message": decision["assistant_message"]})

            if decision["type"] == "multi_tool_call":
                await _run_multi_tool_call(
                    decision, session_id, queue,
                    session_store=session_store,
                    workflow_registry=workflow_registry,
                    summarize_result_fn=summarize_result_fn,
                )
                await emit_event(queue, {"type": "done"})
                return

            _platform = decision["arguments"].get("platform", decision["tool_name"].replace("run_", "").replace("_competitor_analysis", ""))
            _keyword = decision["arguments"].get("brand", "")
            _count = decision["arguments"].get("count", 5)
            _task_id: int | None = None
            try:
                _task_id = await create_crawl_task(_platform, _keyword, _count)
            except Exception:
                pass  # DB unavailable — continue without task tracking

            try:
                result = await workflow_registry.call_tool(
                    decision["tool_name"],
                    decision["arguments"],
                    lambda payload: emit_event(queue, payload),
                )
                if _task_id is not None:
                    try:
                        await update_crawl_task(_task_id, "done", products_found=result.get("count", _count))
                    except Exception:
                        pass
            except Exception:
                if _task_id is not None:
                    try:
                        await update_crawl_task(_task_id, "failed", error_message="workflow error")
                    except Exception:
                        pass
                raise
            artifact_event = build_artifact_event(result)
            await emit_event(queue, artifact_event)

            try:
                final_message = summarize_result_fn(decision["tool_name"], result)
            except Exception:
                final_message = _fallback_summary_message(result)

            if not isinstance(final_message, str) or not final_message.strip():
                final_message = _fallback_summary_message(result)

            session_store.append_message(session_id, "assistant", final_message)
            await emit_event(queue, {"type": "assistant", "message": final_message})
            await emit_event(queue, {"type": "done"})
        except Exception as exc:
            await emit_event(queue, build_error_event(f"任务执行失败: {exc}"))
            await emit_event(queue, {"type": "done"})
    finally:
        session_store.finish_run(session_id, run_id)


async def _run_multi_tool_call(
    decision: dict,
    session_id: str,
    queue: EventQueue,
    *,
    session_store: SessionStore,
    workflow_registry,
    summarize_result_fn,
) -> None:
    tool_calls = decision["tool_calls"]
    brand = tool_calls[0]["arguments"].get("brand", "") if tool_calls else ""
    count = tool_calls[0]["arguments"].get("count", 5) if tool_calls else 5

    task_ids: list[int | None] = []
    for tc in tool_calls:
        platform = tc["tool_name"].replace("run_", "").replace("_competitor_analysis", "")
        try:
            tid = await create_crawl_task(platform, tc["arguments"]["brand"], tc["arguments"]["count"])
        except Exception:
            tid = None
        task_ids.append(tid)

    async def run_one(tc: dict, task_id: int | None) -> dict:
        platform = tc["tool_name"].replace("run_", "").replace("_competitor_analysis", "")
        try:
            result = await workflow_registry.call_tool(
                tc["tool_name"], tc["arguments"],
                lambda payload: emit_event(queue, payload),
            )
            if task_id is not None:
                try:
                    await update_crawl_task(task_id, "done", products_found=result.get("count", count))
                except Exception:
                    pass
            return result
        except Exception as exc:
            if task_id is not None:
                try:
                    await update_crawl_task(task_id, "failed", error_message=str(exc))
                except Exception:
                    pass
            raise

    raw_results = await asyncio.gather(
        *[run_one(tc, tid) for tc, tid in zip(tool_calls, task_ids)],
        return_exceptions=True,
    )

    platform_rows: list[tuple[str, list[dict]]] = []
    for tc, result in zip(tool_calls, raw_results):
        platform = tc["tool_name"].replace("run_", "").replace("_competitor_analysis", "")
        if isinstance(result, Exception):
            await emit_event(queue, build_error_event(f"{platform} 分析失败: {result}"))
        else:
            rows = result.get("rows") or []
            platform_rows.append((platform, rows))

    if not platform_rows:
        return

    merged = await write_multi_platform_analysis_csv(platform_rows, brand, count)
    await emit_event(queue, build_artifact_event(merged))

    try:
        final_message = summarize_result_fn("multi_platform", merged)
    except Exception:
        final_message = _fallback_summary_message(merged)

    if not isinstance(final_message, str) or not final_message.strip():
        final_message = _fallback_summary_message(merged)

    session_store.append_message(session_id, "assistant", final_message)
    await emit_event(queue, {"type": "assistant", "message": final_message})
