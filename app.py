from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_service import RUNS, discard_run, get_session_payload, new_run, new_session, run_session_message
from artifacts import ARTIFACTS_DIR
from session_store import ConcurrentRunError


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
TERMINAL_EVENT_TYPES = {"done"}


class MessageRequest(BaseModel):
    message: str


class SafeStaticFiles(StaticFiles):
    async def check_config(self) -> None:
        if self.directory and not Path(self.directory).exists():
            return
        await super().check_config()

    async def get_response(self, path: str, scope):
        if self.directory and not Path(self.directory).exists():
            return Response(status_code=404)
        return await super().get_response(path, scope)


def build_run_event_stream(run_id: str, run, runs: dict, discard_run_fn=discard_run):
    async def event_stream():
        terminal_reached = False
        try:
            while True:
                payload = await run.queue.get()
                terminal_reached = payload.get("type") in TERMINAL_EVENT_TYPES
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if terminal_reached:
                    break
        finally:
            if terminal_reached:
                discard_run_fn(run_id, runs=runs)

    return event_stream()


def schedule_run_cleanup(run_id: str, runs: dict, delay_seconds: float = 60.0, discard_run_fn=discard_run) -> None:
    async def cleanup():
        await asyncio.sleep(delay_seconds)
        discard_run_fn(run_id, runs=runs)

    asyncio.create_task(cleanup())


async def run_session_message_with_cleanup(
    session_id: str,
    run,
    runs: dict,
    run_session_message_fn,
    cleanup_scheduler,
) -> None:
    try:
        await run_session_message_fn(session_id, run.run_id, run.queue)
    finally:
        cleanup_scheduler(run.run_id, runs)


def create_app(
    *,
    frontend_dir: Path = FRONTEND_DIR,
    artifacts_dir: Path = ARTIFACTS_DIR,
    runs: dict = RUNS,
    new_session_fn=new_session,
    get_session_payload_fn=get_session_payload,
    new_run_fn=new_run,
    run_session_message_fn=run_session_message,
    discard_run_fn=discard_run,
    cleanup_scheduler=schedule_run_cleanup,
):
    app = FastAPI()
    app.mount(
        "/static",
        SafeStaticFiles(directory=str(frontend_dir), html=True, check_dir=False),
        name="static",
    )

    @app.get("/")
    async def index():
        index_file = frontend_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<html><body><h1>竞品分析</h1></body></html>")

    @app.post("/api/sessions")
    async def create_session_route():
        session = new_session_fn()
        return {"session_id": session.session_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session_route(session_id: str):
        try:
            return get_session_payload_fn(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.post("/api/sessions/{session_id}/messages")
    async def post_message(session_id: str, request: MessageRequest):
        try:
            run = new_run_fn(session_id, request.message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except ConcurrentRunError as exc:
            raise HTTPException(status_code=409, detail="Session already has an active run") from exc

        asyncio.create_task(
            run_session_message_with_cleanup(
                session_id,
                run,
                runs,
                run_session_message_fn,
                cleanup_scheduler,
            )
        )
        return {"session_id": session_id, "run_id": run.run_id}

    @app.get("/api/sessions/{session_id}/runs/{run_id}/stream")
    async def stream_run(session_id: str, run_id: str):
        run = runs.get(run_id)
        if run is None or run.session_id != session_id:
            raise HTTPException(status_code=404, detail="Run not found")
        return StreamingResponse(
            build_run_event_stream(run_id, run, runs, discard_run_fn),
            media_type="text/event-stream",
        )

    @app.get("/api/download/{filename}")
    async def download_file(filename: str):
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="File not found")

        path = artifacts_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(path, filename=filename)

    return app


app = create_app()
