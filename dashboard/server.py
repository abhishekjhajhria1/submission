"""Web dashboard: start a task, watch every step live, answer questions, approve actions.

The agent runs in a worker thread; its RunLog events are streamed to the browser
over Server-Sent Events. Approvals/questions block the agent thread until the
browser POSTs a response (see events.WebHuman).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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
# When hosted publicly, set FARMHAND_ACCESS_CODE so only people you give the code to can spend LLM quota.
ACCESS_CODE = os.getenv("FARMHAND_ACCESS_CODE", "")


def require_code(x_access_code: str | None = Header(default=None)) -> None:
    if ACCESS_CODE and x_access_code != ACCESS_CODE:
        raise HTTPException(401, "Access code required")


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


@app.get("/farm", response_class=HTMLResponse)
def farm_office():
    """The farm app's own human view, proxied so it also works when only the dashboard port is public."""
    try:
        return HTMLResponse(httpx.get(f"{FARM_URL}/", timeout=5).text)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"farm app not reachable: {e}")


@app.post("/api/runs", dependencies=[Depends(require_code)])
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


@app.get("/api/runs")
def list_runs(limit: int = 12):
    """Recent runs (newest first) for the history panel."""
    items = []
    for folder in sorted(settings.runs_dir.glob("*-*-*"), reverse=True):
        if not (folder / "trace.jsonl").exists():
            continue
        live = folder.name in runs and runs[folder.name]["thread"].is_alive()
        report_file = folder / "report.json"
        if live or not report_file.exists():
            first = json.loads((folder / "trace.jsonl").open(encoding="utf-8").readline() or "{}")
            items.append({"run_id": folder.name, "task": first.get("task", ""), "status": "running" if live else "incomplete"})
        else:
            r = json.loads(report_file.read_text(encoding="utf-8"))
            items.append({"run_id": folder.name, "task": r.get("task", ""), "status": r.get("status"),
                          "duration_s": r.get("duration_s")})
        if len(items) >= limit:
            break
    return items


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, after: int = 0):
    run = runs.get(run_id)
    if not run:
        trace = settings.runs_dir / run_id / "trace.jsonl"
        if run_id != Path(run_id).name or not trace.exists():
            raise HTTPException(404, "unknown run")

        async def replay():  # a finished run from an earlier session: stream its saved trace
            for line in trace.read_text(encoding="utf-8").splitlines()[after:]:
                yield f"data: {line}\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream")
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


@app.post("/api/runs/{run_id}/respond", dependencies=[Depends(require_code)])
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


@app.post("/api/farm/reset", dependencies=[Depends(require_code)])
def farm_reset(body: Reset):
    if any(r["thread"].is_alive() for r in runs.values()):
        raise HTTPException(409, "A task is running")
    reset_sandbox(profile, body.chaos)
    return {"ok": True, "chaos": body.chaos}
