"""Offline tests: the real agent loop + real farm app, with a scripted fake LLM (no API key, no network).

Run: python -m pytest tests -q   (or: python tests/test_offline.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import farmhand.agent as agent_mod  # noqa: E402
from farm_app.app import S, app  # noqa: E402
from farmhand.config import Settings, load_profile  # noqa: E402
from farmhand.events import RunLog, ScriptedHuman  # noqa: E402
from farmhand.tools import ToolRegistry, api_tools, file_tools  # noqa: E402


class FakeLLM:
    """Returns pre-scripted decisions in order (worker and verifier share the queue)."""

    model = "fake"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.on_wait = lambda msg: None

    def decide(self, system, prompt, validate=None):
        d = self.decisions.pop(0)
        if validate:
            validate(d)
        self.usage["calls"] += 1
        return d

    def read_document(self, data, mime):
        return "SHARMA AGRO KENDRA Bill No: SAK/1047 Date: 11/10/2026 TOTAL Rs. 4,015.00 Payment: UPI"


def act(tool, **args):
    return {"evaluation": "", "thought": f"use {tool}", "action": {"tool": tool, "args": args}}


def run(decisions, human=None, chaos=0.0):
    S.reset(chaos, 1)
    profile = load_profile("patil_farms")
    client = TestClient(app)
    agent_mod.build_tools = lambda profile, llm, run_id: ToolRegistry(
        api_tools("", "/openapi.json", run_id, http=client) + file_tools(profile.files_root, llm.read_document))
    settings = Settings()
    log = RunLog("test-" + str(len(decisions)), settings.runs_dir)
    llm = FakeLLM(decisions)
    report = agent_mod.Agent(settings, profile, llm, human or ScriptedHuman(), log).run("test task")
    return report, log


ORDER = dict(buyer_id=1, items=[{"product": "tomato", "qty_kg": 200, "price_per_kg": 27}], delivery_date="2026-10-16")
VERDICT_OK = act("verdict", passed=True, checks=[{"check": "order exists", "expected": "1", "observed": "1", "ok": True}],
                 summary="all good")


def test_happy_path_is_verified():
    report, log = run([
        {**act("get_stock"), "plan": ["check stock", "book", "confirm"], "plan_step": 0},
        act("create_order", **ORDER),
        act("send_message", contact_id=1, text="Booked 200 kg tomato @ Rs 27, total Rs 5,400"),
        act("finish", status="success", summary="Booked and confirmed", outcome={"order_id": 2}),
        act("list_orders", buyer_id=1),
        VERDICT_OK,
    ])
    assert report["status"] == "verified"
    assert S.stock["tomato"]["available_kg"] == 150
    assert len(report["writes"]) == 2
    assert any(e["type"] == "verify_result" and e["passed"] for e in log.events)


def test_policy_gate_blocks_rejected_write():
    human = ScriptedHuman(approve=False)
    cheap = dict(buyer_id=3, items=[{"product": "onion", "qty_kg": 1000, "price_per_kg": 14}], delivery_date="2026-10-14")
    report, _ = run([act("create_order", **cheap), act("finish", status="blocked", summary="Owner rejected")], human)
    assert human.approvals, "approval should have been requested"
    assert report["status"] == "blocked"
    assert len(S.orders) == 1, "no order may be created after a rejection"


def test_verifier_rejection_triggers_repair_round():
    report, log = run([
        act("create_order", **ORDER),
        act("finish", status="success", summary="done"),
        act("verdict", passed=False, checks=[{"check": "buyer informed", "ok": False}], summary="no message sent"),
        act("send_message", contact_id=1, text="Confirmed: 200 kg tomato, Rs 5,400"),
        act("finish", status="success", summary="done, buyer informed"),
        VERDICT_OK,
    ])
    assert report["status"] == "verified"
    rejected = [e for e in log.events if e["type"] == "tool_result" and e["tool"] == "finish" and not e["ok"]]
    assert rejected, "the verifier's rejection must be fed back to the worker"


def test_invalid_tool_is_rejected_by_validator():
    try:
        run([act("delete_everything")])
    except Exception as e:  # FakeLLM has no repair loop, so the validation error surfaces
        assert "unknown tool" in str(e)
    else:
        raise AssertionError("validator should reject unknown tools")


def test_loop_detection_aborts():
    report, _ = run([act("get_stock")] * 6)
    assert report["status"] == "aborted" and "loop" in report["summary"]


def test_idempotent_write_under_chaos_never_duplicates():
    for seed in range(15):
        S.reset(1.0, seed)
        tools = ToolRegistry(api_tools("", "/openapi.json", f"run{seed}", http=TestClient(app)))
        tools.get("create_order").run(ORDER)
        tools.get("create_order").run(ORDER)  # agent retrying the same write
        assert len([o for o in S.orders if o["buyer_id"] == 1]) <= 2  # seed order + at most one new


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
