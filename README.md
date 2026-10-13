# 🌾 FarmHand: an autonomous AI worker for a small farm business

FarmHand takes a task in plain language (English, Hindi or Hinglish), works out what needs to happen,
and **does the work** in the business's real systems. It plans, calls tools, reads documents,
recovers from failures, asks the owner when it should, and finishes only after an **independent verifier**
confirms in those systems that the result is actually there.

It works for **Suresh Patil**, a vegetable and grape farmer near Nashik. His business runs on WhatsApp:
buyers message orders, prices move daily at the mandi, and bills are photos on his phone.

> **Demo video:** _<add link>_

```
"Hotel Annapurna messaged about tomatoes. Handle the order and confirm with them."

 → reads WhatsApp: 200 kg tomatoes for "this Friday"           → 2026-10-16
 → checks stock: 350 kg available                              ✓
 → mandi price: Rs 2,400 per QUINTAL → Rs 24/kg, +3 hotel rule  → Rs 27/kg
 → creates the order (reserves stock, generates invoice)       → PF-INV-2042, Rs 5,400
 → sends the buyer a WhatsApp confirmation
 → re-reads the order to confirm it really exists
 → independent verifier re-checks everything (read-only)       ✅ VERIFIED
```

---

## Quick start

Requirements: Python 3.11+ and a free Gemini API key from https://aistudio.google.com/apikey.

```bash
pip install -r requirements.txt
cp .env.example .env          # then put your GEMINI_API_KEY in .env
python run.py                 # farm app on :8001, dashboard on :8000 (opens the browser)
python -m farmhand.doctor     # optional, in a 2nd terminal: checks key, model, farm app
```

In the dashboard, click an example task (or type your own) and press **Run**. Use the **Chaos** selector to
inject failures. Other entry points:

```bash
python -m farmhand "Handle FreshMart's latest order request." --reset      # CLI (asks you in the terminal)
python -m farmhand "..." --chaos 0.3                                       # with failure injection
python -m eval.run --chaos 0,0.3                                           # eval suite (farm app must be running)
python -m pytest tests -q                                                  # offline tests, no API key needed
```

## Deploy (public demo)

Everything runs as **one web service**: `run.py` starts the farm app internally and serves the dashboard on
the platform's `$PORT`. The demo is **passwordless**. `FARMHAND_MAX_RUNS_PER_DAY` (25 in the blueprint) caps new
tasks so visitors can't drain the free LLM quota; replays of past runs are always available. To lock it down
instead, set `FARMHAND_ACCESS_CODE` and the UI will ask for it.

- **Render (free):** New → Blueprint → select this repo (uses [render.yaml](render.yaml)) and paste
  `GEMINI_API_KEY` when prompted. The free plan sleeps when idle, so the first visit takes about a minute.
- **Hugging Face Spaces / Railway / Fly.io:** use the [Dockerfile](Dockerfile) and set `GEMINI_API_KEY`.

## Demo tasks (same code, no task-specific logic)

| Task | What it shows |
|---|---|
| *Hotel Annapurna messaged about tomatoes. Handle the order and confirm with them.* | Works out the goal, multi-step execution, unit conversion (quintal → kg), date reasoning ("this Friday"), applies the owner's pricing rules, confirms with the buyer |
| *Handle FreshMart's latest order request.* | Grapes are short (100 kg asked, 80 kg in stock). The SOP says the owner decides, so it **asks**, then adapts the order |
| *Sai Traders wants onions at the price they asked for. Book it.* | Rs 14/kg is below the Rs 15 floor, so the **approval gate in code** pauses the action. Approve: it books. Reject: it stops and reports "blocked" |
| *Sharma Agro wala naya fertilizer bill mere expenses mein daal do.* | Hinglish. Finds the right scan among unnamed files, **reads the PDF**, converts DD/MM dates, records the expense once |

Turn on **Chaos** and run any of them: the farm app starts returning 503s, responding slowly, and failing writes,
sometimes *after* saving. The agent still completes the task without duplicates.

---

## Architecture

```
          ┌──────────────────────── Dashboard (FastAPI + SSE) / CLI / Eval harness ───────────────────────┐
 task ───▶│  live steps · plan · memory · approve/reject · answer questions · verification report          │
          └───────────────▲──────────────────────────────────────────────────────┬────────────────────────┘
                          │ events (RunLog → trace.jsonl)                          │ human responses
          ┌───────────────┴──────────────────────────────────────────────────────▼────────────────────────┐
          │ AGENT RUNTIME (farmhand/agent.py)                                                              │
          │                                                                                                │
          │   ┌─ decide ──────────────┐   ┌─ policy gate ──────┐   ┌─ act ─────────────┐   ┌─ observe ─┐ │
          │   │ 1 LLM call → JSON:     │──▶│ writes checked by  │──▶│ tool executor:    │──▶│ result →  │ │
          │   │ evaluation, thought,   │   │ profile policy.py; │   │ retries, idem-    │   │ history   │ │
          │   │ plan, remember, action │   │ human approval     │   │ potency keys      │   │ + memory  │ │
          │   └──────────▲─────────────┘   └────────────────────┘   └───────────────────┘   └─────┬─────┘ │
          │              └───────────── loop detection · step budget · error budget ◀──────────────┘       │
          │                                                                                                │
          │   finish(success) ──▶ VERIFIER: fresh context, other model, read-only tools, checks the systems │
          │                       pass → "verified"   fail → findings sent back to worker (1 repair round)  │
          └───────────────┬───────────────────────────────────────┬────────────────────────────────────────┘
                          │ tools auto-generated from OpenAPI      │ file tools (PDF/images → vision model)
          ┌───────────────▼────────────────────┐    ┌──────────────▼─────────────┐   ┌──────────────────────┐
          │ Farm office app (sandbox)           │    │ workspace/ (owner's docs)  │   │ Company profile      │
          │ stock · mandi prices · WhatsApp ·   │    │ scanned receipts           │   │ profile.toml: SOP,    │
          │ orders/invoices · expenses · chaos  │    └────────────────────────────┘   │ prices, limits        │
          └─────────────────────────────────────┘                                     │ policy.py: approvals │
                                                                                      └──────────────────────┘
```

| Path | What it is |
|---|---|
| `farmhand/agent.py` | The loop (`Agent`) and the independent `Verifier` |
| `farmhand/tools.py` | Tools generated from any OpenAPI spec, file tools, retries and idempotency |
| `farmhand/llm.py` | LLM clients (Gemini, any OpenAI-compatible API): JSON decisions, document reading, pacing, backoff, token accounting |
| `farmhand/doctor.py` | Setup check: key, available models, farm app, document reading |
| `farmhand/prompts.py` | Worker and verifier prompts |
| `farmhand/events.py` | Event stream (dashboard/CLI/trace) and human channels (web, CLI, scripted) |
| `profiles/patil_farms/` | Everything business-specific: who the owner is, SOP rules, price floors, approval policy |
| `farm_app/` | The sandbox business system: real HTTP API, validation, state, failure injection, live HTML view |
| `dashboard/` | Live web UI |
| `eval/` | Task suite with ground-truth checks against the farm database |
| `tests/` | Offline tests of the real loop with a scripted fake LLM |

## How a run works

1. **Context.** The worker gets: the goal, the company profile (owner, SOP rules), the tool catalogue, its
   current plan, its working memory, owner answers so far, and the recent history (the last 3 results in full,
   older ones abbreviated, earlier steps summarised by memory).
2. **Decide.** One LLM call returns a JSON decision: an evaluation of the previous result, a thought, an optional
   revised plan, facts to `remember`, and exactly **one** action. Invalid JSON or an unknown tool is sent back
   to the model with the error (up to 2 repairs).
3. **Gate.** Before any write, the runtime calls the profile's `policy.py`. If it returns a reason (price below
   floor, order above Rs 25,000), the run pauses for the owner's approval. A rejection comes back to the agent
   as a tool error it must respect.
4. **Act.** The tool executor calls the API, handling transient failures itself (see Reliability).
5. **Observe.** The result goes into history. The runtime watches for repeated identical calls (warning at 3,
   abort at 5) and consecutive failures (warning at 3, abort at 6). The step budget is 30.
6. **Finish.** `finish(success)` is a *claim*, not the end. The verifier starts fresh, with no access to the
   worker's reasoning. It gets the goal, the claim, the log of writes and the owner's decisions, and can only
   use read-only tools (enforced: its tool registry contains no write tools). It returns per-check
   expected/observed results. If it rejects the claim, its findings go back to the worker for one repair round.
   Final statuses: `verified`, `unverified`, `blocked`, `failed`, `aborted`.

## Key design decisions

- **Tools come from the app's OpenAPI spec, not hand-written wrappers.** `tools.py` turns every operation into a
  tool, with a compact signature built from the JSON schema (types, required fields, enums, descriptions).
  GET operations are `read`; anything else is `write`. Pointing FarmHand at another API gives it new tools
  without code changes. The read/write split drives retries, approvals and the verifier's read-only mode.
- **API + files instead of pixel clicking.** The brief allows APIs, files or a simulated app. APIs are what a
  production AI employee should prefer when they exist: they are deterministic, cheap, and give exact error
  messages. (Browser use is the next step, for systems with no API. See "What's next".)
- **One action per step, with reflection built in.** Each decision starts with `evaluation` (did the last step
  work?) before `thought`. That makes the agent check results, and makes every decision explainable in the trace.
- **Safety rules are code, not prompt.** The prompt *tells* the model about price floors, but `policy.py`
  *enforces* them. The model cannot argue its way past an approval gate.
- **Two layers of reliability.** Deterministic faults are handled deterministically, in code. Semantic problems
  (wrong product name, insufficient stock, a rejected approval) are handled by the model's reasoning.
- **Verify, don't trust.** Agents often claim success they didn't achieve. A separate verifier with a fresh
  context and read-only tools avoids the worker grading its own homework. The eval also measures whether the
  verifier agrees with ground truth.
- **Personalisation lives in a profile.** `profiles/<business>/profile.toml` holds the owner, language notes,
  SOP rules and limits; `policy.py` holds approval rules. This is the "programmable AI employee" idea: the agent
  is generic, and the business is configuration.
- **A sandbox with real failure modes.** `farm_app` is a real web service with validation and state, not a mock
  inside the agent. Chaos mode reproduces what breaks automations in practice, including the hardest case:
  a write that **succeeds but reports failure**.

## Reliability: what happens when things go wrong

| Failure | Handled by |
|---|---|
| 503 / slow response / network error on a read | Executor retries automatically (2 retries, backoff). Shown as a badge in the UI |
| 500 or 504 on a write | Executor retries with the **same Idempotency-Key** (a hash of run + tool + args). If the first attempt had actually saved, the server replays the original response, so **no duplicate**. Shown as "idempotent replay" |
| A write still failing after retries | Result says **"OUTCOME UNKNOWN: check state before retrying"**. The agent reads (e.g. `list_orders`) before deciding |
| Validation error (bad date, unknown product, insufficient stock) | The server's error is passed through verbatim. The agent fixes its arguments or changes plan |
| LLM 429 / 5xx / network | `llm.py` backs off (honouring the API's retry delay) and spaces calls out for the free tier |
| LLM model overloaded or out of quota | **Model failover chain** (`FARMHAND_MODEL=a,b,c`): two 503s → that step uses the next model; a daily quota hit → the model is dropped for the rest of the run, with no pointless waiting |
| Malformed model output | Re-asked with the exact validation error, up to 2 times |
| Agent stuck repeating itself | Warning at 3 identical calls, abort at 5. Error budget of 6 consecutive failures. 30-step budget |
| Claimed success that isn't real | Independent verifier, plus one repair round |

## Evaluation

`python -m eval.run --chaos 0,0.3` resets the farm, runs each task with a **scripted owner** (canned answers,
approve or reject), then checks the farm's **database** directly: the right records, exactly once, with the right
values, the right stock levels, and the buyer informed. The suite covers 5 cases, including approve vs. reject.
It reports success rate per chaos level, steps, LLM calls, failures injected, and **how often the verifier
agreed with ground truth**.

**Latest run** (Flash-Lite worker + Flash-Lite verifier, 5 scenarios × chaos 0% / 30%, full table in
[eval/RESULTS.md](eval/RESULTS.md)):

| Metric | Result |
|---|---|
| Work actually done right (database ground truth) | **10 / 10** |
| Fully correct (right work **and** right final status) | **8 / 10** (30% chaos: 5 / 5) |
| Verifier agreed with ground truth | **8 / 8** runs where it ran |
| Faults injected and absorbed (503s, slow responses, lost writes) | up to 3 per run, no duplicates |
| Avg LLM steps per task | ~5 |

The two misses, analysed:
- `order_approval` (0%): the work was finished and correct, but **this machine's internet connection dropped**
  (`ConnectError` on every model) during verification, so the run aborted. That's infrastructure, not the agent.
- `order_rejected` (0%): after the owner said "no", the lite model **sent the buyer an unrequested counter-offer**
  and reported `success` instead of `blocked`. Nothing was booked (ground truth correct), but it overstepped.
  The prompt forbids this. The cheaper model follows that rule only some of the time, and the verifier didn't
  flag the extra message. Next steps: a stronger worker model, and a deterministic "no writes to this contact after an owner
  rejection" policy.

## What real runs taught me (and what I changed)

| Observed in a real run | Change |
|---|---|
| Free-tier Gemini allows **20 requests/day per model**, and returns waves of 503 "overloaded" | Model **failover chain**; fail fast on a daily quota instead of sleeping; **batched reads** (up to 4 independent reads per step) cut the Annapurna task from 7 LLM steps to 4 |
| The verifier **rejected a correct order**: it saw 150 kg of tomato left and concluded "sold 200 kg with only 150 available". It mistook the after-state for the before-state | Verifier prompt now spells out that reads include the worker's own writes. Re-verifying the same run passed ("350 kg before the order"). This is why the eval measures **verifier agreement with ground truth** |
| A verifier **outage** was treated as a rejection, sending correct work back for "repair" | Outages now end the run as `unverified`, with no repair round |
| Retrying a write with the same idempotency key failed validation, because the first attempt had already reserved the stock | The replay check runs **before** validation, as payment APIs do |
| Models are unreliable at weekday arithmetic ("this Friday", "kal") | An explicit 8-day calendar is in the context |
| In a "rejected" test, the (lite) model asked the owner, got **"No"**, and then **still tried to book the ₹14 order** | Nothing needed: the **code-level approval gate blocked it**, which is exactly why policy lives in code rather than the prompt. I also tightened the prompt ("no" stops that line of action; no unrequested side actions; never wait for third-party replies) |
| The model asked "do you approve?" *and then* the gate asked again (double prompt) | The SOP now says approvals are requested automatically when booking, so the model doesn't need to ask separately |
| One model checking its own family's work shares blind spots | The verifier can run on a different model (`FARMHAND_VERIFIER_MODEL`, default Flash-Lite), which also has a separate quota |

## Known limitations

- **API + files only.** There is no browser or desktop control yet, so systems without an API are out of reach.
- **Sandbox, not real WhatsApp.** The farm app stands in for WhatsApp Business, the mandi price feed and an
  accounting tool. The tools are generated from OpenAPI, so connecting real ones is an integration task rather
  than an agent change.
- **Single run at a time**, in memory. There is no persistence of agent memory across tasks, and no
  multi-tenant isolation.
- **Free-tier LLM pacing.** Calls are spaced about 4 s apart, so a task takes 1–2 minutes. That limit comes from
  the free tier, not the design.
- **The verifier uses the same model family** as the worker, so it may share blind spots. The ground-truth
  eval exists partly to measure this.
- **Policies are Python functions per profile.** Fine for a prototype; a production system would want a
  declarative policy language that non-engineers can edit.
- **Small eval:** 5 cases × 2 chaos levels, limited by free-tier quota.

## What I'd build next

1. **Real channels:** a WhatsApp Business API webhook so buyers' messages trigger tasks automatically, and voice
   notes in Marathi/Hindi (the farmer's real interface).
2. **Browser and computer-use tools** for systems without APIs (government portals, the e-NAM mandi platform),
   behind the same read/write, approval and verifier framework.
3. **Long-term memory:** buyer preferences, negotiated prices, payment reliability, learned from past runs.
4. **Proactive work:** a daily run ("any new orders? any unpaid invoices? stock running low?").
5. **Declarative policies with an audit log**, plus per-action dry-runs, so the owner can see exactly what
   will change before approving.
6. **A bigger eval:** many paraphrases per task, adversarial messages (prompt injection inside WhatsApp text),
   regression tracking per model and prompt version.

## Assumptions

- Prices follow the owner's rule: Nashik APMC modal price ÷ 100 (quintal → kg) + markup by buyer type
  (hotel +3, retailer +2, trader +0). Fresh produce invoices carry no GST.
- The sandbox clock is fixed to Tuesday 2026-10-13 (in `profile.toml`), so "this Friday" and "tomorrow" are
  reproducible.
- Bills are recorded with the total amount due as printed. Dates on Indian documents are DD/MM/YYYY.
- "Confirm with the buyer" means a WhatsApp message with quantities, rate, total, delivery date and invoice number.

## Models, APIs, frameworks

| Component | Used for |
|---|---|
| **Google Gemini** via `google-genai`: `gemini-3.8-flash`, failing over to 3.7 / 3.6 / 3.5 Flash; thinking level `low` | Worker and verifier decisions (JSON mode); transcribing scanned PDFs (multimodal) |
| **Gemini Flash-Lite** (`gemini-3.1-flash-lite` worker, `gemini-3.5-flash-lite` verifier) | What the **demo video and eval** ran on: the free tier allows only 20 requests/day on the main Flash models, and Flash-Lite passed every task anyway |
| Any **OpenAI-compatible API** (optional: Groq, OpenRouter, OpenAI, Ollama) | Same decisions via `FARMHAND_PROVIDER=openai`. PDFs then use a built-in text-layer extractor |
| **FastAPI + Uvicorn** | Farm sandbox app and dashboard |
| **httpx** | API tool calls |
| **Pydantic** | Request validation in the farm app (and the OpenAPI schemas the tools are generated from) |
| Vanilla HTML/JS + Server-Sent Events | Live dashboard (no build step) |

No agent framework (LangChain, browser-use, etc.) is used. The loop, tools, policy gate, verifier and eval
are written from scratch so every behaviour is explicit and easy to change. AI coding assistance (Claude)
was used during development.
