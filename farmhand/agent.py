"""The agent loop: decide -> (policy gate) -> act -> observe -> ... -> finish -> independent verification.

One LLM call per step returns a JSON decision (evaluation of the last result, thought,
plan, facts to remember, next action). The runtime, not the model, owns: the step
budget, loop detection, approval gates, retries/idempotency (in tools.py), and the
decision to accept a "success" only after an independent verifier agrees.
"""
from __future__ import annotations

import json
import time
from collections import Counter

from .config import Profile, Settings
from .events import Human, RunLog
from .llm import LLM, LLMError
from .prompts import VERIFIER_STEP, VERIFIER_SYSTEM, WORKER_STEP, WORKER_SYSTEM
from .tools import ToolRegistry, ToolResult, api_tools, file_tools

CONTROL_TOOLS = {"ask_human", "finish"}
FINISH_STATUSES = {"success", "failed", "blocked"}
MAX_CONSECUTIVE_ERRORS = 6
REPEAT_WARN, REPEAT_ABORT = 3, 5
MAX_REPAIR_ROUNDS = 1
MAX_BATCH = 4  # independent reads per step; writes and control actions always go alone


def build_tools(profile: Profile, llm: LLM, run_id: str) -> ToolRegistry:
    api = profile.data["api"]
    return ToolRegistry(api_tools(api["base_url"], api["openapi_path"], run_id)
                        + file_tools(profile.files_root, llm.read_document))


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _validator(tools: ToolRegistry, control: set[str]):
    """Validate a decision and normalise it to decision["actions"] (a list; only reads may be batched)."""
    def validate(d: dict) -> None:
        actions = d.get("actions", d.get("action"))
        if isinstance(actions, dict):
            actions = [actions]
        if not isinstance(actions, list) or not actions:
            raise ValueError('missing "actions": [{"tool": "...", "args": {...}}]')
        if len(actions) > MAX_BATCH:
            raise ValueError(f"at most {MAX_BATCH} actions per step")
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("tool"), str):
                raise ValueError('each action must look like {"tool": "...", "args": {...}}')
            name = action["tool"]
            if name not in control and not tools.get(name):
                raise ValueError(f"unknown tool {name!r}. Valid tools: {', '.join([*tools.tools, *sorted(control)])}")
            if action.get("args") is None:
                action["args"] = {}
            args = action["args"]
            if not isinstance(args, dict):
                raise ValueError('"args" must be a JSON object')
            if name == "finish" and (args.get("status") not in FINISH_STATUSES or not args.get("summary")):
                raise ValueError("finish needs status (success|failed|blocked) and summary")
            if name == "ask_human" and not args.get("question"):
                raise ValueError("ask_human needs a question")
            if name == "verdict" and not isinstance(args.get("passed"), bool):
                raise ValueError("verdict needs passed: true|false and checks")
        if len(actions) > 1:
            unbatchable = [a["tool"] for a in actions if a["tool"] in control or tools.get(a["tool"]).effect != "read"]
            if unbatchable:
                raise ValueError(f"only [read] tools can be batched; {unbatchable} must be the only action in its step")
        d["actions"] = actions
    return validate


class Agent:
    def __init__(self, settings: Settings, profile: Profile, llm: LLM, human: Human, log: RunLog,
                 verifier_llm: LLM | None = None):
        self.settings, self.profile, self.llm, self.human, self.log = settings, profile, llm, human, log
        self.verifier_llm = verifier_llm or llm  # a different model reduces shared blind spots
        self.max_steps = profile.limit("max_steps", 30)

    # ------------------------------------------------------------------ main loop

    def run(self, task: str) -> dict:
        started = time.monotonic()
        self.task = task
        self.plan: list[str] = []
        self.plan_step = 0
        self.memory: dict = {}
        self.history: list[dict] = []
        self.owner_input: list[str] = []
        self.writes: list[dict] = []
        self.warnings: list[str] = []
        self.approved: set[str] = set()
        self.calls: Counter[str] = Counter()
        self.repairs = 0
        self.verdict: dict | None = None
        self.llm.on_wait = self.verifier_llm.on_wait = lambda msg: self.log.emit("waiting", message=msg)

        self.tools = build_tools(self.profile, self.llm, self.log.run_id)
        self.log.emit("run_started", task=task, model=self.llm.model, business=self.profile.name,
                      tools=[{"name": t.name, "effect": t.effect} for t in self.tools.tools.values()])
        system = WORKER_SYSTEM.format(**self.profile.prompt_vars(), tools=self.tools.docs())
        validate = _validator(self.tools, CONTROL_TOOLS)

        final, consecutive_errors, step = None, 0, 0
        while final is None:
            step += 1
            if step > self.max_steps:
                final = self._final("aborted", f"Stopped: step budget of {self.max_steps} used up.")
                break
            try:
                decision = self.llm.decide(system, self._step_prompt(step), validate)
            except LLMError as e:
                self.log.emit("error", message=str(e))
                final = self._final("aborted", f"Stopped: {e}")
                break
            self._absorb(decision)
            actions = decision["actions"]
            for i, action in enumerate(actions):
                name, args = action["tool"], action["args"]
                label = str(step) if len(actions) == 1 else f"{step}.{i + 1}"
                effect = "control" if name in CONTROL_TOOLS else self.tools.get(name).effect
                self.log.emit("step", step=label, evaluation=decision.get("evaluation", "") if i == 0 else "",
                              thought=decision.get("thought", "") if i == 0 else "(batched read)",
                              tool=name, args=args, effect=effect)

                if name == "finish":
                    final = self._on_finish(args, label)
                    break
                if name == "ask_human":
                    answer = self.human.ask(args["question"], list(args.get("options") or []))
                    self.owner_input.append(f"Q: {args['question']} -> A: {answer}")
                    self.log.emit("answer", step=label, answer=answer)
                    self._record(label, name, args, ToolResult(True, output=f"Owner answered: {answer}"))
                    break

                result = self._act(label, name, args)
                self._record(label, name, args, result)
                consecutive_errors = 0 if result.ok else consecutive_errors + 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    final = self._final("aborted", f"Stopped after {consecutive_errors} consecutive failed actions.")
                elif consecutive_errors >= 3:
                    self.warnings.append(f"{consecutive_errors} actions in a row have failed. Step back: re-read "
                                         "the errors, check the current state, and choose a different approach.")
                if self.calls[_compact([name, args])] >= REPEAT_ABORT:
                    final = self._final("aborted", f"Stopped: '{name}' was called {REPEAT_ABORT} times with "
                                                   "identical arguments (stuck in a loop).")
                if final:
                    break

        final.update(steps=step, duration_s=round(time.monotonic() - started, 1), usage=self._usage(),
                     model=self.llm.model, memory=self.memory, writes=self.writes, owner_input=self.owner_input,
                     verification=self.verdict)
        (self.log.dir / "report.json").write_text(json.dumps(final, indent=2, ensure_ascii=False, default=str),
                                                  encoding="utf-8")
        self.log.emit("run_finished", report=final)
        return final

    # ------------------------------------------------------------------ pieces

    def _act(self, step: int | str, name: str, args: dict) -> ToolResult:
        tool = self.tools.get(name)
        signature = _compact([name, args])
        self.calls[signature] += 1
        if self.calls[signature] >= REPEAT_WARN:
            self.warnings.append(f"You have called {name} with identical arguments {self.calls[signature]} times. "
                                 "Repeating it will not change the result; try something different.")

        if tool.effect == "write" and signature not in self.approved:
            reason = self.profile.requires_approval(name, args, self.profile.data)
            if reason:
                approved, note = self.human.approve(f"{name} {_compact(args)}", reason)
                verdict = "APPROVED" if approved else "REJECTED"
                self.owner_input.append(f"Approval for {name} {_compact(args)} ({reason}) -> {verdict}"
                                        + (f": {note}" if note else ""))
                self.log.emit("approval_result", step=step, approved=approved, note=note, reason=reason)
                if not approved:
                    return ToolResult(False, error=f"The owner REJECTED this action ({reason})."
                                                   + (f" Owner's note: {note}." if note else "")
                                                   + " Do not attempt it again in this form.")
                self.approved.add(signature)

        result = tool.run(args)
        if tool.effect == "write" and result.ok:
            self.writes.append({"step": step, "tool": name, "args": args, "result": result.output})
        return result

    def _on_finish(self, args: dict, step: int | str) -> dict | None:
        status = args["status"]
        if status != "success":
            return self._final(status, args["summary"], args.get("outcome"))
        self.log.emit("verify_started", step=step, claim=args)
        self.verdict = Verifier(self.verifier_llm, self.tools.read_only(), self.profile, self.log).verify(
            self.task, args, self.writes, self.owner_input)
        self.log.emit("verify_result", **self.verdict)
        if self.verdict["passed"]:
            return self._final("verified", args["summary"], args.get("outcome"))
        if self.verdict.get("unavailable"):  # don't send correct work back for "repair" because of an outage
            return self._final("unverified", args["summary"] + " (Independent verification was unavailable.)",
                               args.get("outcome"))
        if self.repairs < MAX_REPAIR_ROUNDS:
            self.repairs += 1
            failed = [c for c in self.verdict.get("checks", []) if not c.get("ok")]
            self._record(step, "finish", args, ToolResult(
                False, error="An independent verifier REJECTED your claim of success. "
                             f"Verifier summary: {self.verdict.get('summary')}. Failed checks: {_compact(failed)}. "
                             "Fix the problems (or explain why the verifier is wrong by finishing with status failed)."))
            return None
        return self._final("unverified", args["summary"], args.get("outcome"))

    def _usage(self) -> dict:
        usage = {k: v for k, v in self.llm.usage.items() if k != "models"}
        usage["models"] = dict(self.llm.usage.get("models", {}))
        if self.verifier_llm is not self.llm:
            for k in ("calls", "input_tokens", "output_tokens"):
                usage[k] += self.verifier_llm.usage[k]
            for model, n in self.verifier_llm.usage.get("models", {}).items():
                usage["models"][f"{model} (verifier)"] = n
        return usage

    def _final(self, status: str, summary: str, outcome: dict | None = None) -> dict:
        return {"run_id": self.log.run_id, "task": self.task, "status": status, "summary": summary,
                "outcome": outcome or {}}

    def _absorb(self, decision: dict) -> None:
        if isinstance(decision.get("plan"), list) and decision["plan"]:
            self.plan = [str(p) for p in decision["plan"]]
        if isinstance(decision.get("plan_step"), int):
            self.plan_step = decision["plan_step"]
        if self.plan:
            self.log.emit("plan", plan=self.plan, current=self.plan_step)
        if isinstance(decision.get("remember"), dict) and decision["remember"]:
            self.memory.update(decision["remember"])
            self.log.emit("memory", memory=self.memory)

    def _record(self, step: int | str, name: str, args: dict, result: ToolResult) -> None:
        self.history.append({"step": step, "tool": name, "args": args, "ok": result.ok, "result": result})
        self.log.emit("tool_result", step=step, tool=name, ok=result.ok, text=result.text(1500),
                      retries=result.retries, notes=result.notes)

    def _step_prompt(self, step: int) -> str:
        plan = "\n".join(f"  {'✓' if i < self.plan_step else '→' if i == self.plan_step else ' '} {i + 1}. {p}"
                         for i, p in enumerate(self.plan)) or "  (none yet: write one)"
        lines = []
        recent = self.history[-14:]
        for i, h in enumerate(recent):
            full = i >= len(recent) - 3  # latest results in full, older ones abbreviated
            lines.append(f"#{h['step']} {h['tool']} {_compact(h['args'])}\n   -> {h['result'].text(2500 if full else 300)}")
        if len(self.history) > len(recent):
            lines.insert(0, f"({len(self.history) - len(recent)} earlier steps omitted; key facts are in MEMORY)")
        warnings = "".join(f"\nWARNING: {w}" for w in self.warnings)
        self.warnings = []
        return WORKER_STEP.format(owner=self.profile.data["owner"], task=self.task, plan=plan,
                                  memory=_compact(self.memory), owner_input=_compact(self.owner_input) or "none",
                                  history="\n".join(lines) or "(nothing yet)", warnings=warnings + "\n",
                                  step=step, max_steps=self.max_steps)


class Verifier:
    """Independent, read-only check of the worker's claim. Fresh context: it sees the goal,
    the claim and the write log, but none of the worker's reasoning."""

    def __init__(self, llm: LLM, tools: ToolRegistry, profile: Profile, log: RunLog):
        self.llm, self.tools, self.profile, self.log = llm, tools, profile, log
        self.max_steps = profile.limit("verifier_max_steps", 10)

    def verify(self, task: str, claim: dict, writes: list[dict], owner_input: list[str]) -> dict:
        system = VERIFIER_SYSTEM.format(**self.profile.prompt_vars(), tools=self.tools.docs())
        validate = _validator(self.tools, {"verdict"})
        history: list[str] = []
        for step in range(1, self.max_steps + 1):
            prompt = VERIFIER_STEP.format(task=task, report=_compact(claim), writes=_compact(writes) or "none",
                                          owner_input=_compact(owner_input) or "none",
                                          history="\n".join(history) or "(none yet)", step=step,
                                          max_steps=self.max_steps)
            try:
                decision = self.llm.decide(system, prompt, validate)
            except LLMError as e:  # infrastructure failure, not a judgement on the work
                return {"passed": False, "unavailable": True, "checks": [],
                        "summary": f"Verification could not run: {e}"}
            for action in decision["actions"]:
                name, args = action["tool"], action["args"]
                self.log.emit("verify_step", step=step, thought=decision.get("thought", ""), tool=name, args=args)
                if name == "verdict":
                    checks = [c for c in args.get("checks", []) if isinstance(c, dict)]
                    passed = bool(args["passed"]) and all(c.get("ok", False) for c in checks)
                    return {"passed": passed, "checks": checks, "summary": args.get("summary", "")}
                result = self.tools.get(name).run(args)
                self.log.emit("verify_tool_result", step=step, tool=name, ok=result.ok, text=result.text(1500))
                history.append(f"#{step} {name} {_compact(args)}\n   -> {result.text(2500)}")
        return {"passed": False, "checks": [], "summary": "Verifier ran out of steps before reaching a verdict."}
