# The Fixer — 12-Day Execution Plan

**Deadline:** Aug 31, 2026 @ 5:00pm PDT (verified on the Devpost page Aug 19)
**Target submit date:** Aug 29 — Aug 30/31 are verification only, not engineering.
**Builder:** solo.

---

## The one thing that matters

Everything in this project exists to make one 90-second sequence real and reproducible:

> The agent investigates a problem it was not told the answer to → forms a hypothesis →
> applies a fix → **verification fails** → it recognises the failure, revises the
> hypothesis, applies a different fix → verifies real metric recovery.

If that sequence runs live against data the agent genuinely had to explore, we have a
submission. Every other feature is optional decoration. The rule for the whole 12 days:

> **If removing it doesn't lower our chance of winning, cut it.**

---

## Inverted sequencing (vs. the original spec)

The original plan had 12 milestones of specification before code. With 12 days that
fails. We invert: build the thinnest end-to-end slice first, then deepen.

| Original order | Our order |
|---|---|
| Spec everything → build | Build the slice → spec only what's ambiguous |
| Simulator with 8 incidents up front | 1 incident working, then widen to 6 |
| Architecture designed, then implemented | Simplest thing that works, harden on Day 7 |
| UI late | UI Day 6 — it *is* the demo |

---

## Locked technical decisions

Made now so we never re-litigate them mid-sprint.

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 (via `uv`) | ADK is Python-first; 3.12 is the safest for GCP libs |
| Agent framework | **Google ADK 2.x** (`google-adk`) | Satisfies the "Google Agent Framework" requirement |
| Model | **`gemini-3.5-flash`** via Gemini API / Vertex | Only public 3.5 ID as of Aug 2026; 3.5 Pro not yet released. Rule says "3.5 or newer" |
| API | FastAPI + SSE | SSE streams the mission timeline to the UI without websocket complexity |
| Data | SQLAlchemy async — **SQLite local, Cloud SQL Postgres in prod** | Zero-friction local iteration (no Docker daemon needed); Cloud SQL is an accepted GCP service |
| Frontend | Vite + React + Tailwind, built to static, served by FastAPI | One deployable unit, one Cloud Run service |
| Hosting | **Cloud Run** | Explicitly listed in the rules; trivial container deploy |
| Async fan-out | `asyncio.gather` first; **Pub/Sub** only if Day 7 has room | Parallelism story without a distributed-systems detour |

**Google Cloud services used (requirement: ≥1):** Cloud Run + Cloud SQL, with Vertex AI
for the model. Pub/Sub and Cloud Logging are stretch.

### The simulator model (the key design call)

NovaCart is **not** a static dataset with a hard-coded answer. It is a generator:

```
world = base_traffic × active_modifiers(incident, applied_remediations)
```

- At scenario start, an incident is selected and its effect is baked into generated
  rows from time T onward. Nobody tells the agent which incident.
- When the agent applies a remediation, the simulator recomputes the modifier set and
  generates **new rows going forward**. A correct fix makes the metric genuinely
  recover; a wrong fix genuinely doesn't.
- Verification queries real rows over a real time window. Recovery is not scripted —
  it is a consequence of whether the agent was right.

This is what stops the demo being an illusion, and it is the single most important
technical decision in the project.

---

## Day-by-day

Each day has a **gate**: if the gate isn't met by end of day, we cut scope from later
days rather than sliding the schedule.

### Day 1 — Wed Aug 19 — Simulator core
- Repo scaffold, `uv` env, schema (sessions, orders, payments, deployments, config, flags, services, logs, tickets).
- Data generator producing realistic baseline traffic.
- Incident A: **payment config regression** (iOS-only checkout failures).
- Modifier engine: applying/reverting a remediation changes future generated data.
- **Gate:** I can start a scenario, query conversion by platform, and see iOS depressed. Applying the correct fix in code makes it recover.

### Day 2 — Thu Aug 20 — Tool layer + first Gemini call
- Read tools: `query_analytics`, `query_orders`, `query_payments`, `query_logs`, `query_deployments`, `query_configuration`, `query_support_tickets`, `query_infrastructure`.
- Action tools: `rollback_deployment`, `update_configuration`, `restore_configuration`, `disable_feature`, `restart_service`.
- Verification tools: `check_conversion`, `check_error_rate`, `check_payment_success`, `compare_metrics`.
- Every tool returns structured JSON and carries `permission` / `risk` / `reversibility` metadata.
- Confirm ADK + `gemini-3.5-flash` round-trips with a real tool call.
- **Gate:** an ADK agent calls a real tool against the simulator and gets real numbers back.

### Day 3 — Fri Aug 21 — Vertical slice ⚑ CRITICAL
- Mission model + orchestrator loop: objective → investigate → hypothesise → act → verify.
- One mission runs start to finish on Incident A.
- **Gate:** given only *"Our conversion rate dropped. Find out why and fix it,"* the agent finds the root cause unaided and fixes it. **If this gate slips, everything after Day 5 gets cut.**

### Day 4 — Sat Aug 22 — Hypotheses + the recovery loop ⚑ CRITICAL
- Competing hypotheses with confidence that moves as evidence arrives.
- Failed remediation is a first-class event: record it, downgrade the hypothesis, don't repeat the action, pick a new strategy.
- Tool success vs. mission success strictly separated.
- **Gate:** the signature sequence runs live — first fix fails verification, agent recovers, second fix verifies.

### Day 5 — Sun Aug 23 — Incident library
- Incidents B–F: bad deployment, feature-flag mistake, third-party API degradation, fraud filter over-blocking, DB performance.
- Distractor signals so correlation alone isn't enough.
- Incident generator: random root cause + symptoms + distractors + valid remediations.
- **Gate:** agent solves ≥4 of 6 incident types without per-incident prompt tuning.

### Day 6 — Mon Aug 24 — Mission Control UI
- Ops console, deliberately not a chat window: mission header, live timeline, hypothesis panel with confidence bars, evidence count, before/after metric chart.
- SSE streaming so the judge watches it happen in real time.
- **Gate:** the whole mission is legible on screen to someone who has never seen the project.

### Day 7 — Tue Aug 25 — Safety + parallelism
- Approval gate for high-risk/irreversible actions, with the agent stating action, reason, risk, rollback availability.
- Parallel investigation via `asyncio.gather`.
- Termination states: SUCCESS / FAILED / BLOCKED / REQUIRES_HUMAN / INSUFFICIENT_EVIDENCE / TIMEOUT / SAFETY_LIMIT.
- **Gate:** agent provably cannot execute an unauthorised action; parallel investigation visibly shortens missions.

### Day 8 — Wed Aug 26 — Evaluation harness
- Run N missions headless across randomised incidents; record root-cause accuracy, correct-remediation rate, false-completion rate, recovery rate, unauthorised actions.
- **Gate:** a real results table, from real runs. We publish only what the runs support — no aspirational numbers.

### Day 9 — Thu Aug 27 — Deploy
- Containerise, Cloud Run deploy, Cloud SQL, secrets, public URL.
- **Gate:** a stranger with the URL can run a mission end to end.

### Day 10 — Fri Aug 28 — Demo + docs
- Record the ~4-minute video (backend visibly running on Google Cloud).
- Architecture diagram, README with spin-up instructions.
- **Gate:** video recorded and watchable; README lets someone else run it.

### Day 11 — Sat Aug 29 — **SUBMIT**
- Devpost writeup, all artifacts attached, submitted.
- **Gate:** submission is in. Not drafted — submitted.

### Day 12 — Sun Aug 30 — Buffer / adversarial audit
- Attack our own submission as a hostile judge. Fix only what's damaging.
- Bonus opportunities if time: technical write-up, social post.

### Aug 31 — Verification only
Re-read the official rules, confirm the submission still satisfies them, confirm links
work. **No code changes.**

---

## Scope already cut

Named explicitly so they don't creep back in.

- BigQuery (Cloud SQL covers the requirement; BQ adds latency and dialect risk)
- Pub/Sub unless Day 7 finishes early
- Multi-tenant auth / user accounts
- More than 6 incident types
- Mission replay as a separate product surface (the timeline already shows it)
- Any second demo domain

## Risks

| Risk | Mitigation |
|---|---|
| ADK 2.0 API differs from my assumptions | Day 2 is deliberately early; read the installed package source, not blog posts |
| Agent can't find root causes reliably | Day 3/4 gates catch this while there's still time to simplify the incident space |
| Demo looks scripted | The generator design above — plus we show it solving a *randomly selected* incident |
| Deadline rules change mid-event | Re-verify Devpost on Day 9 and Aug 31 |
| Cloud Run deploy eats a day | Day 9 is dedicated; nothing else scheduled |

---

## Running log

### Day 1 — Aug 19 — DONE, gate passed

Simulator built and verified. `scripts/smoke_day1.py` proves all three properties:

```
conversion overall   3.68%  ->  2.60%
  ios                3.61%  ->  0.63%     <- isolated
  web                4.02%                <- untouched
  android            3.46%                <- untouched
PAY_CFG_3021 errors  83 in the last hour

rollback deployment 8472   -> tool reports success, ios stays 0.77%
restore config             -> ios 3.84%, errors 0, 108% recovery to baseline
```

354,595 sessions generated. The failed-rollback moment is now a property of the
world model, not a script: config is versioned separately from deployments, so
rolling back the deploy genuinely cannot restore it.

**Issue found:** `start_scenario` takes ~40s at 250 sessions/min over 24h of
history. Too slow to sit behind a "Start Mission" click in the demo. Fix on Day 6:
pre-generate the pre-incident history once, cache the SQLite file, and per
scenario copy it and generate only the incident window. History before the
incident is incident-independent, so one cached baseline serves every scenario.

**Bug fixed:** the diurnal traffic curve could evaluate negative, collapsing
traffic to 1 session/min and making short-window conversion rates statistically
meaningless. Now bounded to roughly [0.5, 1.5].
