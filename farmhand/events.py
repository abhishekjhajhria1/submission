"""Run events (consumed by the CLI, the web dashboard and the trace file) and human channels."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol


class RunLog:
    """Append-only event stream for one run; also persisted to runs/<id>/trace.jsonl."""

    def __init__(self, run_id: str, runs_dir: Path):
        self.run_id = run_id
        self.events: list[dict] = []
        self.dir = runs_dir / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cond = threading.Condition()
        self.listeners: list[Callable[[dict], None]] = []

    def emit(self, kind: str, **data) -> dict:
        event = {"seq": len(self.events), "time": time.time(), "type": kind, **data}
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()
        with open(self.dir / "trace.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        for listener in self.listeners:
            listener(event)
        return event

    def wait_for(self, after: int, timeout: float = 15.0) -> list[dict]:
        with self._cond:
            if len(self.events) <= after:
                self._cond.wait(timeout)
            return self.events[after:]


class Human(Protocol):
    def ask(self, question: str, options: list[str]) -> str: ...
    def approve(self, action: str, reason: str) -> tuple[bool, str]: ...


class CLIHuman:
    def ask(self, question: str, options: list[str]) -> str:
        print(f"\n🙋 FarmHand asks: {question}")
        for i, o in enumerate(options, 1):
            print(f"   {i}. {o}")
        answer = input("   your answer > ").strip()
        return options[int(answer) - 1] if answer.isdigit() and 0 < int(answer) <= len(options) else answer

    def approve(self, action: str, reason: str) -> tuple[bool, str]:
        print(f"\n⚠️  Approval needed: {action}\n   Why: {reason}")
        answer = input("   approve? [y/N] (optionally add a note after the y/n) > ").strip()
        return answer.lower().startswith("y"), answer[1:].strip()


class ScriptedHuman:
    """For the eval harness: canned answers, and approve/reject by policy."""

    def __init__(self, answer: str = "Use your best judgement.", approve: bool = True):
        self.answer, self.approve_all = answer, approve
        self.asked: list[str] = []
        self.approvals: list[str] = []

    def ask(self, question: str, options: list[str]) -> str:
        self.asked.append(question)
        return self.answer

    def approve(self, action: str, reason: str) -> tuple[bool, str]:
        self.approvals.append(action)
        return self.approve_all, "" if self.approve_all else "Rejected by the owner."


class WebHuman:
    """Blocks the agent thread until the dashboard posts a response."""

    def __init__(self, log: RunLog):
        self.log = log
        self.pending: dict[str, dict] = {}
        self.cancelled = False

    def cancel(self) -> None:
        """Owner pressed Stop: release anything the agent is waiting on; the loop then stops itself."""
        self.cancelled = True
        for slot in list(self.pending.values()):
            slot["response"] = {"answer": "The owner stopped this task.", "approved": False,
                                "note": "The owner stopped this task."}
            slot["event"].set()

    def _wait(self, kind: str, **data) -> dict:
        if self.cancelled:
            return {"answer": "The owner stopped this task.", "approved": False}
        request_id = uuid.uuid4().hex[:8]
        slot = {"event": threading.Event(), "response": None}
        self.pending[request_id] = slot
        self.log.emit(kind, request_id=request_id, **data)
        slot["event"].wait()
        del self.pending[request_id]
        return slot["response"]

    def respond(self, request_id: str, response: dict) -> bool:
        slot = self.pending.get(request_id)
        if not slot:
            return False
        slot["response"] = response
        slot["event"].set()
        return True

    def ask(self, question: str, options: list[str]) -> str:
        return str(self._wait("question", question=question, options=options).get("answer", ""))

    def approve(self, action: str, reason: str) -> tuple[bool, str]:
        r = self._wait("approval_request", action=action, reason=reason)
        return bool(r.get("approved")), str(r.get("note", ""))
