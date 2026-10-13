"""Glue used by the CLI, the dashboard and the eval harness."""
from __future__ import annotations

import time
import uuid

import httpx

from .agent import Agent
from .config import Profile, Settings, load_profile
from .events import Human, RunLog
from .llm import make_llm


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def reset_sandbox(profile: Profile, chaos: float = 0.0, seed: int = 0) -> None:
    """Sandbox only: restore seed data and set the chaos level (not a tool the agent can see)."""
    httpx.post(profile.data["api"]["base_url"] + "/__admin/reset", json={"chaos": chaos, "seed": seed},
               timeout=10).raise_for_status()


def run_task(task: str, human: Human, log: RunLog | None = None, settings: Settings | None = None,
             profile: Profile | None = None) -> dict:
    settings = settings or Settings()
    profile = profile or load_profile(settings.profile)
    log = log or RunLog(new_run_id(), settings.runs_dir)
    try:
        return Agent(settings, profile, make_llm(settings), human, log).run(task)
    except Exception as e:  # never leave a dashboard run hanging without a final event
        log.emit("error", message=f"{type(e).__name__}: {e}")
        report = {"run_id": log.run_id, "task": task, "status": "crashed", "summary": str(e), "outcome": {}}
        log.emit("run_finished", report=report)
        raise
