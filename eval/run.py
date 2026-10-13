"""Run the eval suite: python -m eval.run [--tasks order_basic,expense_hinglish] [--chaos 0,0.3] [--repeats 1]

Needs the farm app running (python run.py, or uvicorn farm_app.app:app --port 8001).
Writes eval/results/<timestamp>.json and eval/RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from farmhand.config import Settings, load_profile
from farmhand.events import RunLog
from farmhand.runner import new_run_id, reset_sandbox, run_task

from .tasks import TASKS

HERE = Path(__file__).parent


def run_one(task, chaos: float, seed: int, settings: Settings, profile) -> dict:
    reset_sandbox(profile, chaos, seed)
    human = task.human()
    log = RunLog(new_run_id(), settings.runs_dir)
    try:
        report = run_task(task.prompt, human, log, settings, profile)
    except Exception as e:
        report = {"status": "crashed", "summary": str(e), "usage": {}, "steps": 0}
    state = httpx.get(profile.data["api"]["base_url"] + "/__admin/state", timeout=10).json()
    checks = [{"check": name, "ok": bool(ok), "observed": observed} for name, ok, observed in task.check(state, human)]
    ground_truth = all(c["ok"] for c in checks)
    status = report.get("status")
    usage = report.get("usage") or {}
    verification = report.get("verification") or {}
    return {
        "task": task.id, "chaos": chaos, "seed": seed, "run_id": log.run_id,
        "agent_status": status, "expected_status": "/".join(task.expect_status),
        "success": ground_truth and status in task.expect_status,
        "ground_truth": ground_truth,
        # verifier agreement: only meaningful when the verifier actually ran (not on an LLM outage)
        "verifier_said_ok": (status == "verified") if verification and not verification.get("unavailable") else None,
        "verifier_unavailable": bool(verification.get("unavailable")),
        "checks": checks, "steps": report.get("steps"), "llm_calls": usage.get("calls"),
        "tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        "duration_s": report.get("duration_s"), "faults_injected": len(state.get("chaos_log", [])),
    }


def summarise(rows: list[dict], model: str) -> str:
    lines = [f"# Eval results\n\nModel: `{model}` · {time.strftime('%Y-%m-%d %H:%M')} · {len(rows)} runs\n",
             "| Task | Chaos | Result | Agent status | Steps | LLM calls | Faults injected | Failed checks |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        failed = "; ".join(c["check"] for c in r["checks"] if not c["ok"]) or "-"
        lines.append(f"| {r['task']} | {r['chaos']:.0%} | {'✅' if r['success'] else '❌'} | {r['agent_status']} | "
                     f"{r['steps']} | {r['llm_calls']} | {r['faults_injected']} | {failed} |")
    lines.append("")
    for chaos in sorted({r["chaos"] for r in rows}):
        group = [r for r in rows if r["chaos"] == chaos]
        ok = sum(r["success"] for r in group)
        lines.append(f"- **Chaos {chaos:.0%}: {ok}/{len(group)} tasks fully correct** "
                     f"(avg {sum(r['steps'] or 0 for r in group) / len(group):.1f} steps)")
    judged = [r for r in rows if r["verifier_said_ok"] is not None]
    if judged:
        agree = sum(r["verifier_said_ok"] == r["ground_truth"] for r in judged)
        lines.append(f"- **Verifier agreed with ground truth in {agree}/{len(judged)} runs** where it ran")
    outages = sum(r.get("verifier_unavailable", False) for r in rows)
    if outages:
        lines.append(f"- Verifier unavailable (LLM outage) in {outages} run(s): reported as `unverified`")
    lines.append(f"- Ground truth correct (work actually done right) in "
                 f"{sum(r['ground_truth'] for r in rows)}/{len(rows)} runs")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", default="all")
    p.add_argument("--chaos", default="0,0.3")
    p.add_argument("--repeats", type=int, default=1)
    args = p.parse_args()

    settings = Settings()
    profile = load_profile(settings.profile)
    tasks = TASKS if args.tasks == "all" else [t for t in TASKS if t.id in args.tasks.split(",")]
    rows = []
    for chaos in [float(c) for c in args.chaos.split(",")]:
        for task in tasks:
            for rep in range(args.repeats):
                print(f"▶ {task.id} chaos={chaos:.0%} run {rep + 1}...", flush=True)
                row = run_one(task, chaos, seed=rep + 1, settings=settings, profile=profile)
                rows.append(row)
                print(f"  {'✅' if row['success'] else '❌'} status={row['agent_status']} steps={row['steps']} "
                      f"faults={row['faults_injected']} " +
                      "; ".join(f"{'✓' if c['ok'] else '✗'} {c['check']}" for c in row["checks"]), flush=True)

    (HERE / "results").mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (HERE / "results" / f"{stamp}.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    report = summarise(rows, settings.model)
    (HERE / "RESULTS.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
