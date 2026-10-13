# Eval results

Model: `gemini-3.1-flash-lite,gemini-3.5-flash-lite` · 2026-10-14 01:52 · 10 runs

| Task | Chaos | Result | Agent status | Steps | LLM calls | Faults injected | Failed checks |
|---|---|---|---|---|---|---|---|
| order_basic | 0% | ✅ | verified | 4 | 6 | 0 | - |
| order_low_stock | 0% | ✅ | verified | 7 | 10 | 0 | - |
| order_approval | 0% | ❌ | aborted | 5 | 4 | 0 | - |
| order_rejected | 0% | ❌ | verified | 5 | 8 | 0 | - |
| expense_hinglish | 0% | ✅ | verified | 4 | 9 | 0 | - |
| order_basic | 30% | ✅ | verified | 5 | 7 | 3 | - |
| order_low_stock | 30% | ✅ | verified | 5 | 7 | 3 | - |
| order_approval | 30% | ✅ | verified | 5 | 7 | 3 | - |
| order_rejected | 30% | ✅ | blocked | 4 | 4 | 0 | - |
| expense_hinglish | 30% | ✅ | verified | 5 | 9 | 0 | - |

- **Chaos 0%: 3/5 tasks fully correct** (avg 5.0 steps)
- **Chaos 30%: 5/5 tasks fully correct** (avg 4.8 steps)
- **Verifier agreed with ground truth in 8/8 runs** where it ran
- Ground truth correct (work actually done right) in 10/10 runs
