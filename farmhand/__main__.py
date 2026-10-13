"""CLI: python -m farmhand "Hotel Annapurna messaged about tomatoes, handle the order" [--chaos 0.3] [--reset]"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings, load_profile
from .events import CLIHuman, RunLog
from .runner import new_run_id, reset_sandbox, run_task


def printer(event: dict) -> None:
    t = event["type"]
    if t == "run_started":
        print(f"\n🌾 FarmHand working for {event['business']} ({event['model']})\n   Task: {event['task']}")
    elif t == "plan" and event.get("current") == 0:
        print("   Plan: " + " | ".join(event["plan"]))
    elif t == "step":
        print(f"\n[{event['step']}] 💭 {event['thought']}\n    → {event['tool']} {json.dumps(event['args'], ensure_ascii=False)}")
    elif t == "tool_result":
        mark = "✓" if event["ok"] else "✗"
        print(f"    {mark} {event['text'][:300]}")
    elif t == "memory":
        print(f"    🧠 {json.dumps(event['memory'], ensure_ascii=False)[:300]}")
    elif t == "approval_result":
        print(f"    {'✅ approved' if event['approved'] else '⛔ rejected'} ({event['reason']})")
    elif t == "waiting":
        print(f"    ⏳ {event['message']}")
    elif t == "verify_started":
        print("\n🔎 Independent verification...")
    elif t == "verify_step":
        print(f"   [v{event['step']}] {event['tool']} {json.dumps(event['args'], ensure_ascii=False)}")
    elif t == "verify_result":
        print(f"   {'✅ VERIFIED' if event['passed'] else '❌ NOT VERIFIED'}: {event['summary']}")
        for c in event.get("checks", []):
            print(f"     {'✓' if c.get('ok') else '✗'} {c.get('check')}: {c.get('observed')}")
    elif t == "error":
        print(f"    ‼ {event['message']}")
    elif t == "run_finished":
        r = event["report"]
        print(f"\n==== {r['status'].upper()} ====\n{r['summary']}")
        if r.get("usage"):
            print(f"steps={r.get('steps')} llm_calls={r['usage']['calls']} "
                  f"tokens={r['usage']['input_tokens'] + r['usage']['output_tokens']} time={r.get('duration_s')}s")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="farmhand")
    p.add_argument("task")
    p.add_argument("--reset", action="store_true", help="reset the sandbox farm to seed data first")
    p.add_argument("--chaos", type=float, default=None, help="sandbox failure injection level 0..1 (implies --reset)")
    args = p.parse_args()

    settings = Settings()
    profile = load_profile(settings.profile)
    if args.reset or args.chaos is not None:
        reset_sandbox(profile, args.chaos or 0.0)
    log = RunLog(new_run_id(), settings.runs_dir)
    log.listeners.append(printer)
    run_task(args.task, CLIHuman(), log, settings, profile)
    print(f"trace: {log.dir}")


if __name__ == "__main__":
    main()
