from __future__ import annotations

import asyncio
from dataclasses import dataclass

from primary_agent import decide_next_step, summarize_workflow_result
from session_store import SessionStore
from workflow_registry import build_default_registry


SESSION_STORE = SessionStore()
WORKFLOW_REGISTRY = build_default_registry()
RUNS: dict[str, "AgentRun"] = {}


@dataclass
class AgentRun:
    run_id: str
    session_id: str
    queue: asyncio.Queue


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
) -> AgentRun:
    session_store.append_message(session_id, "user", message)
    run_id = session_store.start_run(session_id)
    run = AgentRun(run_id=run_id, session_id=session_id, queue=asyncio.Queue())
    runs[run.run_id] = run
    return run


async def emit_event(queue: asyncio.Queue, payload: dict) -> None:
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


async def run_session_message(
    session_id: str,
    run_id: str,
    queue: asyncio.Queue,
    *,
    session_store: SessionStore = SESSION_STORE,
    workflow_registry=WORKFLOW_REGISTRY,
    decide_next_step_fn=decide_next_step,
    summarize_result_fn=summarize_workflow_result,
    runs: dict[str, AgentRun] = RUNS,
) -> None:
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

        result = await workflow_registry.call_tool(
            decision["tool_name"],
            decision["arguments"],
            lambda payload: emit_event(queue, payload),
        )
        artifact_event = build_artifact_event(result)
        await emit_event(queue, artifact_event)

        final_message = summarize_result_fn(decision["tool_name"], result)
        session_store.append_message(session_id, "assistant", final_message)
        await emit_event(queue, {"type": "assistant", "message": final_message})
        await emit_event(queue, {"type": "done"})
    finally:
        session_store.finish_run(session_id, run_id)
        runs.pop(run_id, None)
