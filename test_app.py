from pathlib import Path
from threading import Event
from types import SimpleNamespace

import asyncio

from fastapi.testclient import TestClient

from app import ARTIFACTS_DIR, app, build_run_event_stream, create_app
from artifacts import ARTIFACTS_DIR as SHARED_ARTIFACTS_DIR
from session_store import ConcurrentRunError


def _make_run(run_id="run-123", session_id="session-123"):
    return SimpleNamespace(run_id=run_id, session_id=session_id, queue=asyncio.Queue())


def test_requirements_lists_fastapi_stack():
    text = Path("requirements.txt").read_text(encoding="utf-8")

    assert "fastapi" in text
    assert "uvicorn" in text
    assert "openai" in text


def test_create_session_returns_session_id():
    class FakeSession:
        session_id = "session-123"

    test_app = create_app(new_session_fn=lambda: FakeSession())
    client = TestClient(test_app)

    response = client.post("/api/sessions")

    assert response.status_code == 200
    assert response.json() == {"session_id": "session-123"}


def test_download_route_returns_404_for_missing_file():
    client = TestClient(app)

    response = client.get("/api/download/missing.csv")

    assert response.status_code == 404


def test_app_and_artifacts_share_same_absolute_artifacts_dir():
    assert ARTIFACTS_DIR == SHARED_ARTIFACTS_DIR
    assert ARTIFACTS_DIR.is_absolute()


def test_get_session_returns_message_history_and_slots():
    test_app = create_app(
        get_session_payload_fn=lambda session_id: {
            "session_id": session_id,
            "messages": [{"role": "user", "content": "帮我看一下 Blackview 的竞品"}],
            "slots": {"platform": None, "brand": "Blackview", "count": None},
            "active_run_id": None,
        }
    )
    client = TestClient(test_app)

    response = client.get("/api/sessions/session-123")

    assert response.status_code == 200
    assert response.json()["slots"]["brand"] == "Blackview"


def test_post_message_returns_run_id_and_schedules_background_work():
    called = Event()
    run = _make_run()

    def fake_new_run(session_id, message):
        assert session_id == "session-123"
        assert message == "帮我看一下 Blackview 的竞品"
        return run

    async def fake_run_session_message(session_id, run_id, queue):
        assert session_id == "session-123"
        assert run_id == "run-123"
        assert queue is run.queue
        called.set()

    test_app = create_app(new_run_fn=fake_new_run, run_session_message_fn=fake_run_session_message)
    client = TestClient(test_app)

    response = client.post(
        "/api/sessions/session-123/messages",
        json={"message": "帮我看一下 Blackview 的竞品"},
    )

    assert response.status_code == 200
    assert response.json() == {"session_id": "session-123", "run_id": "run-123"}
    assert called.wait(1)


def test_post_message_schedules_fallback_cleanup_after_background_completion():
    cleanup_called = Event()
    run = _make_run()
    runs = {run.run_id: run}

    def fake_new_run(session_id, message):
        assert session_id == "session-123"
        assert message == "帮我看一下 Blackview 的竞品"
        return run

    async def fake_run_session_message(session_id, run_id, queue):
        assert session_id == "session-123"
        assert run_id == run.run_id
        assert queue is run.queue

    def fake_cleanup_scheduler(run_id, run_map):
        assert run_id == run.run_id
        assert run_map is runs
        run_map.pop(run_id, None)
        cleanup_called.set()

    test_app = create_app(
        runs=runs,
        new_run_fn=fake_new_run,
        run_session_message_fn=fake_run_session_message,
        cleanup_scheduler=fake_cleanup_scheduler,
    )
    client = TestClient(test_app)

    response = client.post(
        "/api/sessions/session-123/messages",
        json={"message": "帮我看一下 Blackview 的竞品"},
    )

    assert response.status_code == 200
    assert cleanup_called.wait(1)
    assert run.run_id not in runs


def test_post_message_returns_409_for_concurrent_run_conflict():
    def fake_new_run(_session_id, _message):
        raise ConcurrentRunError("session session-123 already active: run-999")

    test_app = create_app(new_run_fn=fake_new_run)
    client = TestClient(test_app)

    response = client.post(
        "/api/sessions/session-123/messages",
        json={"message": "帮我看一下 Blackview 的竞品"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Session already has an active run"}


def test_stream_route_emits_assistant_and_done_frames():
    runs = {}
    run = _make_run(run_id="run-stream", session_id="session-123")
    runs[run.run_id] = run
    asyncio.run(run.queue.put({"type": "assistant", "message": "你想分析哪个平台？目前我支持 Amazon。"}))
    asyncio.run(run.queue.put({"type": "done"}))

    test_app = create_app(runs=runs)
    client = TestClient(test_app)

    response = client.get("/api/sessions/session-123/runs/run-stream/stream")

    assert response.status_code == 200
    assert 'data: {"type": "assistant", "message": "你想分析哪个平台？目前我支持 Amazon。"}\n\n' in response.text
    assert 'data: {"type": "done"}\n\n' in response.text
    assert run.run_id not in runs


def test_stream_route_emits_error_and_trailing_done_frames():
    runs = {}
    run = _make_run(run_id="run-error", session_id="session-123")
    runs[run.run_id] = run
    asyncio.run(run.queue.put({"type": "error", "message": "任务执行失败: worker exploded"}))
    asyncio.run(run.queue.put({"type": "done"}))

    test_app = create_app(runs=runs)
    client = TestClient(test_app)

    response = client.get("/api/sessions/session-123/runs/run-error/stream")

    assert response.status_code == 200
    assert 'data: {"type": "error", "message": "任务执行失败: worker exploded"}\n\n' in response.text
    assert 'data: {"type": "done"}\n\n' in response.text
    assert run.run_id not in runs


def test_stream_route_keeps_run_after_client_disconnect_before_terminal_event():
    run = _make_run(run_id="run-disconnect")
    runs = {run.run_id: run}
    asyncio.run(run.queue.put({"type": "assistant", "message": "你想分析哪个平台？目前我支持 Amazon。"}))
    stream = build_run_event_stream(run.run_id, run, runs)
    chunk = asyncio.run(anext(stream))
    asyncio.run(stream.aclose())

    assert 'data: {"type": "assistant", "message": "你想分析哪个平台？目前我支持 Amazon。"}\n\n' == chunk
    assert run.run_id in runs


def test_stream_route_cleans_up_when_client_closes_after_terminal_chunk():
    run = _make_run(run_id="run-terminal-close")
    runs = {run.run_id: run}
    asyncio.run(run.queue.put({"type": "done"}))
    stream = build_run_event_stream(run.run_id, run, runs)
    chunk = asyncio.run(anext(stream))
    asyncio.run(stream.aclose())

    assert 'data: {"type": "done"}\n\n' == chunk
    assert run.run_id not in runs


def test_static_route_returns_404_when_frontend_directory_is_missing(tmp_path):
    missing_frontend = tmp_path / "frontend"
    test_app = create_app(frontend_dir=missing_frontend)
    client = TestClient(test_app)

    response = client.get("/static/app.js")

    assert response.status_code == 404


def test_frontend_files_exist():
    assert Path("frontend/index.html").exists()
    assert Path("frontend/app.js").exists()
    assert Path("frontend/styles.css").exists()
