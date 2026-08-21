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

### Day 2 — Aug 20 — DONE (gate partially blocked on credentials)

18 tools built across three kinds, all passing `scripts/smoke_day2.py` (13/13):

| kind | tools |
|---|---|
| read (9) | conversion_funnel, payments, orders, logs, deployments, configuration, feature_flags, support_tickets, infrastructure |
| act (6) | rollback_deployment, update_configuration, restore_configuration, disable_feature, restart_service, issue_goodwill_refunds |
| verify (3) | check_conversion, check_payment_success, check_error_rate |

Verified without needing a model: every tool produces a valid ADK declaration
with its parameters and description; every tool returns real numbers from the
live world; the ledger records each call with permission/risk/reversibility;
the safety gate refuses `issue_goodwill_refunds` (HIGH/IRREVERSIBLE) and
records the refusal; no tool result leaks scenario internals.

**Design rules that turned out to matter:**
- Verification tools return numbers and sample sizes, never a verdict. No field
  in any result says "solved" — that judgement belongs to the agent, from data.
- Every action tool requires a `reason` argument. Unreviewable actions are not
  allowed to exist.
- Action results carry an explicit caveat that a tool succeeding is not evidence
  the problem is fixed.
- Comparisons are always "same window 24h ago", never "before the incident",
  so no tool can leak when the incident began.

**Realism fix:** added ordinary config churn — 9 unrelated settings change in a
typical 24h window, some by release scripts. Without it, "what changed
recently?" returned exactly one row and the investigation collapsed into a
single lookup. The agent now has to reason about which change could actually
produce the symptom it sees.

**Not a bug, worth recording:** ADK 2.7 emits `parameters_json_schema` rather
than the legacy `parameters` field. The first version of the gate read the old
field and reported zero parameters on all 18 tools, which looked like a total
failure. Worth knowing before trusting any ADK example written against 1.x.

**Blocked:** the last gate item — an ADK agent making a real tool call — needs
model credentials. `scripts/run_mission.py` is written and wired; it detects the
backend, and reports cleanly instead of failing when none is configured.

### Day 3 — Aug 21 — infrastructure DONE, agent gate BLOCKED

Credentials deferred to the hackathon credit project, so the two gates that
need a model (Day 3, Day 4) cannot run yet. Everything around them is built and
tested, so those gates become a single command when credentials land.

**Mission state is now structured, not prose.** Five reasoning tools let the
agent record findings, put forward hypotheses with confidence, revise that
confidence with a stated reason, assess whether a remediation actually worked,
and conclude. That turns the agent's thinking into data Mission Control can
render and the grader can score — without exposing raw chain-of-thought. A
rejected hypothesis is as valuable a record as a confirmed one: it is the
visible evidence the agent changed its mind when the data said so.

Actions are recorded on the mission automatically inside `invoke`, not by the
agent reporting them. What happened must not depend on the agent choosing to be
honest about it.

**Added `wait_for_traffic`.** A remediation only affects sessions served after
it. Without an explicit wait the agent would verify seconds after acting, see
old traffic, and wrongly conclude its fix failed. Under evaluation the wait
jumps the sim clock; in the demo it is an honest wait and reads as the agent
monitoring its change.

**Built a deterministic heuristic agent** (`agent/oracle.py`). Not a mock — it
drives the real tools, world and Mission over the same path the LLM will. It
exists so the pipeline can be regression-tested with no credentials, and so the
LLM has a baseline to be measured against. "Solved 9 of 10" means little alone;
"solved 9 of 10 where a shallow heuristic solves 4" is a claim about reasoning.

**Pulled Day 8's grader forward.** It sits outside the agent, reads the world
directly, and is the only component allowed to know the incident.

Full run, 12/12 checks:

```
isolated to ios -> PAY_CFG_3021 -> logs name 'legacy_v2'
H1 deployment 8472        0.65
H2 config provider_profile 0.45
  rollback_deployment(8472)          -> 0.872% vs 4.131%   INEFFECTIVE
  H1 rejected (0.05), H2 raised (0.85)
  restore_configuration(...)          -> 4.281% vs 3.548%   effective
SUCCESS  root_cause_identified=True  recovery_ratio=1.053  false_completion=False
22 tool calls, 139 sim minutes
```

**Negative control passes too:** a mission that declares SUCCESS having done
nothing is caught — `metric_recovered=False`, `false_completion=True`. The
grader cannot be satisfied by an agent that simply says it succeeded.

### Schedule change

Days 3 and 4 are the two critical gates and both need a model. Rather than
idle, the model-independent work moves forward:

| was | now |
|---|---|
| Day 4 — hypotheses + recovery loop | deferred until credentials |
| Day 5 — incidents B–F | **pulled to next** — no model needed |
| Day 6 — Mission Control UI | **pulled forward** — the oracle produces real events to render |
| Day 8 — evaluation harness | grader already built |

When credentials arrive, Days 3–4 become: run `scripts/run_mission.py`, compare
the LLM's grade against the oracle's on the same seeds, and tune. The scoring,
the incident library and the UI will already exist.

**Fallback if credentials slip past Aug 25:** switch to a Gemini API key on the
existing project (`alarm-72df8` already has the Gemini API enabled and billing
on). That is a 30-second change and costs only the credit, not the schedule.

### Day 5 (pulled forward) — Aug 21 — DONE, gate passed

Six incidents, validated mechanically by `scripts/validate_incidents.py` (20/20).

The design rule that shaped the whole library: **the correct fix must differ
across incidents.** If every answer were "restore the config", an agent would
score well by pattern-matching NovaCart rather than by reasoning, and the
evaluation numbers would mean nothing. So the six span five different action
types, and `bad_deployment` is deliberately the mirror image of
`payment_config_regression` — there, rolling back the deployment *is* the fix.

| incident | discriminated by | correct fix |
|---|---|---|
| payment_config_regression | platform (ios) + error code | restore_configuration |
| bad_deployment | platform (android) + checkout-svc errors | rollback_deployment |
| feature_flag_mistake | funnel stage — **no failed payments at all** | disable_feature |
| provider_degradation | error code, **nothing changed on our side** | update_configuration |
| fraud_overblock | **region**, invisible to a platform split | restore_configuration |
| connection_pool_exhaustion | even everywhere; **only latency shows it** | restart_service |

Heuristic baseline across all six, two seeds each: **41.7% clean, 0% false
completions, 0 unauthorized actions.** That is the number the LLM has to beat
for "it solves incidents" to be a claim about reasoning.

### Four real bugs this shook out

**1. The simulation was not reproducible.** Sim time ran off the wall clock, so
the world depended on how long the code took to execute — the same seed gave
different results, and a slow LLM would face a different scenario than a fast
heuristic. Added a frozen clock mode: live, time passes while the agent thinks
(right for the demo); frozen, it moves only when told to (required for
evaluation). Same seed now reproduces exactly.

**2. The grader could be fooled by a segment-confined incident.** It measured
recovery on the *aggregate*. `fraud_overblock` left DE at 1.04% against a 4.37%
baseline — completely unresolved — while the overall number sat at 0.895 of
baseline and passed. The agent was credited with a success it had not achieved,
which is precisely the failure mode the product exists to prevent. Recovery is
now judged on every segment the incident actually touches.

**3. Verification produced false negatives from noise.** Comparing one 40-minute
window against another 40-minute window from yesterday doubles the sampling
variance, and the threshold sat ~1.2 standard deviations away — so a correct fix
could read as failed. Two changes: the reference is now a wide 3-hour baseline,
and recovery is scored as *the fraction of the observed drop that came back*
rather than an absolute ratio. That is scale-free — the noisier the segment, the
larger the gap it is measured against.

**4. `database disk image is malformed`.** Filesystem-copying a SQLite file is
not safe; pages still in the OS write cache produce a file that opens fine and
fails on first write. Now uses SQLite's own backup API, with a `quick_check` so
a bad cache rebuilds itself rather than poisoning every run.

**Performance fixed too.** History before the incident does not depend on which
incident it is, so it is generated once and cached: **36.7s cold, 2.7s warm.**
That was blocking both the evaluation batch and the demo's "Start Mission"
click.

### Day 6 (pulled forward) — Aug 21 — DONE, gate passed

Mission Control is built and running: FastAPI + SSE backend, React console, one
deployable unit. Verified end to end against a live mission, with screenshots in
`docs/shots/`.

Deliberately an operations console, not a chat window — dense, monospaced, dark,
everything visible at once. A judge should never mistake it for a chatbot with a
nicer theme, and should never wonder where to look.

The single most valuable element is the conversion chart. It carries the whole
argument in one picture: the affected platform's line falls away from the
others, a red marker shows where a remediation was applied and the line *stays
down*, a green marker shows the second remediation and the line comes back.
Nobody has to be told the first fix failed — they watch it fail.

Also on screen: the live timeline; hypotheses with confidence bars, rejected
ones struck through; evidence counters (FAILED FIX in red, RECOVERED in green);
remediations tagged EFFECTIVE/INEFFECTIVE; and the conclusion with root cause
and measured before-and-after. The approval modal is wired for Day 7.

**Two real bugs found by actually looking at it:**

1. **A concurrency bug that would have broken the live demo.** Mission Control
   polls metrics while the agent works, so `tick()` ran from two tasks at once,
   both generated the same minute range, and the inserts collided on primary
   keys — `UNIQUE constraint failed: sessions.id`, returned as a 500 to the
   console mid-mission. Generation is now serialised behind a lock with a
   re-check inside it.

2. **The chart was too noisy to read.** Each 7-minute bucket held only a few
   hundred sessions per platform, so the lines jittered ~20% on sampling noise
   and the actual drop was hard to pick out. Points are now a trailing
   30-minute rolling rate — which is also simply what an operations dashboard
   shows.

Also written: `agent/llm.py`, the model-driven mission runner. Same world, same
tools, same Mission object as the heuristic agent, so the only difference
between them is who decides what to do next — which is what the evaluation is
trying to measure. Includes `max_llm_calls` so an agent looping without
converging terminates as SAFETY_LIMIT rather than running forever, and a
per-tool result summariser so the timeline shows the number that mattered
rather than a wall of JSON.

### Day 7 (pulled forward) — Aug 21 — DONE, gate passed

Safety and parallelism. 16/16 in `scripts/smoke_day7.py`.

**The approval gate now pauses the agent rather than refusing it.** The earlier
version said no and let the agent carry on. That is an audit log, not a control.
Now the agent stops mid-action and waits while a human decides — which is also
the better demo moment: the modal states the action, its risk, whether it can be
undone, and the agent's own stated reason for wanting it.

The wait is bounded at 180 seconds. An agent blocked forever on a dialog nobody
is watching is a hang, not a safety feature; on timeout the action is refused,
the agent is told why, and it continues or ends as REQUIRES_HUMAN.

**The gate is verified against the world, not against the agent's report.** The
test asks the database: after a denied refund, are any orders refunded? Zero,
and the failed-order count is unchanged. All three outcomes are covered —
granted, rejected, nobody answered.

**Added `survey_segments`**, one call that slices conversion by platform,
region, traffic source and app version concurrently, alongside payment failures
and service health. 82ms against 172ms for the same six queries sequentially.

It exists because of a real failure the baseline exposed: the heuristic missed
`fraud_overblock` entirely because it only ever split by platform, and that
fault lives in region. The tool reports an unevenness *ranking* rather than
naming a winner — these dimensions overlap, since every app version belongs to
exactly one platform, so a platform-wide fault always makes app_version look
uneven too and usually more so. Naming one "the answer" would be doing the
agent's reasoning for it, and doing it wrong.

**Baseline improved to 50% clean** (from 41.7%), still 0% false completions. It
now solves `bad_deployment` and `fraud_overblock` reliably. It still fails the
three that need actual reasoning: a fault with no failed payments at all, one
where nothing changed on our side, and one visible only in latency.

Also replaced the heuristic's "most recently changed setting" rule with a
plausibility score — whether the setting names the failing segment, whether the
error text talks about it, whether its value appears in the logs. Configuration
churns constantly, so recency alone is close to no evidence.
