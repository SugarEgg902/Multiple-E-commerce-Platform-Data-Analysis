import asyncio

import pytest

from agent_service import (
    EventQueue,
    RUNS,
    build_artifact_event,
    discard_run,
    new_run,
    new_session,
    run_session_message,
)
from session_store import ConcurrentRunError, SessionStore


def _collect(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_new_session_returns_session_from_store():
    store = SessionStore()

    session = new_session(session_store=store)

    assert session.session_id
    assert store.get_session(session.session_id) == session


def test_new_run_appends_user_message_and_marks_session_active():
    store = SessionStore()
    session = store.create_session()
    runs = {}

    run = new_run(
        session.session_id,
        "帮我看一下 Blackview 的竞品",
        session_store=store,
        runs=runs,
    )

    saved = store.get_session(session.session_id)
    assert run.run_id == saved.active_run_id
    assert saved.messages[-1].content == "帮我看一下 Blackview 的竞品"


def test_new_run_rejects_concurrent_run_without_appending_stray_user_message():
    store = SessionStore()
    session = store.create_session()

    first_run = new_run(
        session.session_id,
        "先分析一次",
        session_store=store,
        runs={},
    )

    with pytest.raises(ConcurrentRunError):
        new_run(
            session.session_id,
            "这条消息不应该被追加",
            session_store=store,
            runs={},
        )

    saved = store.get_session(session.session_id)
    assert saved.active_run_id == first_run.run_id
    assert [message.content for message in saved.messages] == ["先分析一次"]


def test_new_run_rolls_back_active_run_if_queue_creation_fails():
    store = SessionStore()
    session = store.create_session()
    runs = {}

    def fail_queue_factory():
        raise RuntimeError("queue init failed")

    with pytest.raises(RuntimeError, match="queue init failed"):
        new_run(
            session.session_id,
            "帮我看一下 Blackview 的竞品",
            session_store=store,
            runs=runs,
            queue_factory=fail_queue_factory,
        )

    saved = store.get_session(session.session_id)
    assert saved.active_run_id is None
    assert saved.messages == []
    assert runs == {}


def test_event_queue_supports_async_get():
    queue = EventQueue()

    async def exercise():
        producer = asyncio.create_task(queue.put({"type": "assistant", "message": "hello"}))
        item = await queue.get()
        await producer
        return item

    assert asyncio.run(exercise()) == {"type": "assistant", "message": "hello"}
    assert queue.empty()


def test_event_queue_put_skips_cancelled_waiter_and_wakes_next_live_waiter():
    queue = EventQueue()

    async def exercise():
        cancelled_waiter = asyncio.create_task(queue.get())
        live_waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        await queue.put({"type": "assistant", "message": "hello"})
        return await asyncio.wait_for(live_waiter, timeout=0.1)

    assert asyncio.run(exercise()) == {"type": "assistant", "message": "hello"}
    assert queue.empty()


def test_event_queue_cleans_up_cancelled_waiter_without_future_put():
    queue = EventQueue()

    async def exercise():
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    asyncio.run(exercise())

    assert list(queue._waiters) == []
    assert queue.empty()


def test_run_session_message_emits_follow_up_assistant_and_done():
    store = SessionStore()
    session = store.create_session()
    runs = {}
    run = new_run(session.session_id, "帮我看一下 Blackview 的竞品", session_store=store, runs=runs)

    def fake_decide(messages, slots, tool_schemas):
        del messages, slots, tool_schemas
        return {
            "type": "assistant",
            "message": "你想分析哪个平台？目前我支持 Amazon。",
            "slot_updates": {"brand": "Blackview"},
        }

    asyncio.run(
        run_session_message(
            session.session_id,
            run.run_id,
            run.queue,
            session_store=store,
            workflow_registry=None,
            decide_next_step_fn=fake_decide,
            summarize_result_fn=lambda tool_name, tool_result: "",
            runs=runs,
        )
    )

    events = _collect(run.queue)
    assert events == [
        {"type": "assistant", "message": "你想分析哪个平台？目前我支持 Amazon。"},
        {"type": "done"},
    ]
    saved = store.get_session(session.session_id)
    assert saved.active_run_id is None
    assert saved.slots.brand == "Blackview"
    assert saved.messages[-1].content == "你想分析哪个平台？目前我支持 Amazon。"
    assert runs[run.run_id] is run


def test_run_session_message_emits_error_and_cleans_up_on_tool_failure():
    store = SessionStore()
    session = store.create_session()
    runs = {}
    run = new_run(session.session_id, "看 Amazon 的 Blackview，5 个", session_store=store, runs=runs)

    class FailingRegistry:
        def get_tool_schemas(self):
            return [{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}]

        async def call_tool(self, name, arguments, emit):
            del name, arguments, emit
            raise RuntimeError("workflow boom")

    def fake_decide(messages, slots, tool_schemas):
        del messages, slots, tool_schemas
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {"brand": "Blackview", "count": 5},
            "assistant_message": "好的，我开始分析 Amazon 上的 Blackview 竞品。",
            "slot_updates": {
                "platform": "amazon",
                "brand": "Blackview",
                "count": 5,
            },
        }

    asyncio.run(
        run_session_message(
            session.session_id,
            run.run_id,
            run.queue,
            session_store=store,
            workflow_registry=FailingRegistry(),
            decide_next_step_fn=fake_decide,
            summarize_result_fn=lambda tool_name, tool_result: "",
            runs=runs,
        )
    )

    queued = _collect(run.queue)
    assert queued == [
        {"type": "assistant", "message": "好的，我开始分析 Amazon 上的 Blackview 竞品。"},
        {"type": "error", "message": "任务执行失败: workflow boom"},
        {"type": "done"},
    ]
    saved = store.get_session(session.session_id)
    assert saved.active_run_id is None
    assert runs[run.run_id] is run


def test_discard_run_removes_completed_run_from_registry():
    store = SessionStore()
    session = store.create_session()
    runs = {}
    run = new_run(session.session_id, "帮我看一下 Blackview 的竞品", session_store=store, runs=runs)

    discard_run(run.run_id, runs=runs)

    assert runs == {}


def test_run_session_message_emits_artifact_and_final_summary():
    store = SessionStore()
    session = store.create_session()
    run = new_run(session.session_id, "看 Amazon 的 Blackview，5 个", session_store=store, runs={})
    events = []

    async def fake_emit_from_workflow(payload):
        events.append(payload)

    class FakeRegistry:
        def get_tool_schemas(self):
            return [{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}]

        async def call_tool(self, name, arguments, emit):
            assert name == "run_amazon_competitor_analysis"
            assert arguments == {"brand": "Blackview", "count": 5}
            await emit(
                {
                    "type": "tool_status",
                    "tool": "run_amazon_competitor_analysis",
                    "message": "正在抓取 Amazon 商品...",
                }
            )
            return {
                "platform": "amazon",
                "brand": "Blackview",
                "count": 5,
                "rows": [],
                "preview_columns": ["品牌", "ASIN"],
                "preview_rows": [["Blackview", "B0TEST1234"]],
                "filename": "amazon_blackview_5.csv",
                "download_url": "/api/download/amazon_blackview_5.csv",
                "summary": "已完成 5 个竞品分析",
            }

    def fake_decide(messages, slots, tool_schemas):
        del messages, slots, tool_schemas
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {"brand": "Blackview", "count": 5},
            "assistant_message": "好的，我开始分析 Amazon 上的 Blackview 竞品。",
            "slot_updates": {
                "platform": "amazon",
                "brand": "Blackview",
                "count": 5,
            },
        }

    def fake_summary(_tool_name, _tool_result):
        return "已完成 5 个 Amazon 竞品分析，CSV 已生成并可下载。"

    asyncio.run(
        run_session_message(
            session.session_id,
            run.run_id,
            run.queue,
            session_store=store,
            workflow_registry=FakeRegistry(),
            decide_next_step_fn=fake_decide,
            summarize_result_fn=fake_summary,
            runs={run.run_id: run},
        )
    )

    queued = _collect(run.queue)
    assert queued == [
        {"type": "assistant", "message": "好的，我开始分析 Amazon 上的 Blackview 竞品。"},
        {"type": "tool_status", "tool": "run_amazon_competitor_analysis", "message": "正在抓取 Amazon 商品..."},
        build_artifact_event(
            {
                "platform": "amazon",
                "brand": "Blackview",
                "count": 5,
                "rows": [],
                "preview_columns": ["品牌", "ASIN"],
                "preview_rows": [["Blackview", "B0TEST1234"]],
                "filename": "amazon_blackview_5.csv",
                "download_url": "/api/download/amazon_blackview_5.csv",
                "summary": "已完成 5 个竞品分析",
            }
        ),
        {"type": "assistant", "message": "已完成 5 个 Amazon 竞品分析，CSV 已生成并可下载。"},
        {"type": "done"},
    ]
