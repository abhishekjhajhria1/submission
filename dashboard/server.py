"""Web dashboard: start a task, watch every step live, answer questions, approve actions.

The agent runs in a worker thread; its RunLog events are streamed to the browser
over Server-Sent Events. Approvals/questions block the agent thread until the
browser POSTs a response (see events.WebHuman).
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from farmhand.config import Settings, load_profile
from farmhand.events import RunLog, WebHuman
from farmhand.runner import new_run_id, reset_sandbox, run_task

app = FastAPI(title="FarmHand dashboard")
settings = Settings()
profile = load_profile(settings.profile)
FARM_URL = profile.data["api"]["base_url"]
runs: dict[str, dict] = {}
lock = threading.Lock()


class StartRun(BaseModel):
    task: str
    chaos: float | None = None  # None = keep the farm's current state


class Respond(BaseModel):
    request_id: str
    approved: bool | None = None
    note: str = ""
    answer: str = ""


class Reset(BaseModel):
    chaos: float = 0.0


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/api/runs")
def start_run(body: StartRun):
    with lock:
        if any(r["thread"].is_alive() for r in runs.values()):
            raise HTTPException(409, "A task is already running")
        if body.chaos is not None:
            reset_sandbox(profile, body.chaos)
        run_id = new_run_id()
        log = RunLog(run_id, settings.runs_dir)
        human = WebHuman(log)
        thread = threading.Thread(target=_run, args=(body.task, human, log), daemon=True)
        runs[run_id] = {"log": log, "human": human, "thread": thread}
        thread.start()
    return {"run_id": run_id}


def _run(task: str, human: WebHuman, log: RunLog) -> None:
    try:
        run_task(task, human, log, settings, profile)
    except Exception:
        pass  # run_task already emitted the error + a final event


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, after: int = 0):
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    log: RunLog = run["log"]

    async def stream():
        seq = after
        while True:
            batch = await asyncio.to_thread(log.wait_for, seq, 10.0)
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            seq += len(batch)
            if batch and batch[-1]["type"] == "run_finished":
                return
            if not batch:
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/respond")
def respond(run_id: str, body: Respond):
    run = runs.get(run_id)
    if not run or not run["human"].respond(body.request_id, body.model_dump()):
        raise HTTPException(404, "no pending request with that id")
    return {"ok": True}


@app.get("/api/farm")
def farm_state():
    try:
        return httpx.get(f"{FARM_URL}/__admin/state", timeout=5).json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"farm app not reachable at {FARM_URL}: {e}")


@app.post("/api/farm/reset")
def farm_reset(body: Reset):
    if any(r["thread"].is_alive() for r in runs.values()):
        raise HTTPException(409, "A task is running")
    reset_sandbox(profile, body.chaos)
    return {"ok": True, "chaos": body.chaos}
