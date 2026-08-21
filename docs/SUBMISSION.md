# Devpost submission draft

Paste-ready copy for the All Things Agentic Hackathon form. Save it as a draft now
and edit freely — Devpost allows unlimited edits before the deadline.

Anything marked **`[FILL]`** is not known yet. Nothing in here claims something that
has not actually been measured.

---

## Short answers

| field | answer |
|---|---|
| **Project name** | The Fixer |
| **Elevator pitch** | Today's AI tells you what's broken. The Fixer investigates, fixes it, and proves it's fixed — recovering on its own when its first attempt fails. |
| **Track** | The Taskmaster |
| **Which Google SDK / agent framework** | Google ADK (Agent Development Kit) 2.7, Python — `google-adk`, using `LlmAgent` |
| **Which Gemini model** | `gemini-3.5-flash`, via Vertex AI |
| **Google Cloud services** | Cloud Run (hosting), Vertex AI (inference), Cloud Build (container build), Cloud Logging |
| **Hosted project URL** | `[FILL]` — no login required |
| **Repository** | `[FILL]` — public. Verify it opens in an incognito window. |
| **Demo video** | `[FILL]` — YouTube, public, under 4 minutes |
| **Reproducible testing instructions in README?** | Yes — clone-to-running steps, and it runs without credentials using a fallback agent |
| **Date started** | 2026-08-19 |

---

## Inspiration

Every AI operations tool we'd used stops at the same place. It notices something is
wrong, explains it well, produces a confident summary — and then hands the problem
back to a human. The work of actually resolving it never moves.

We wanted to know whether an agent could own the outcome instead of the explanation.
Not "here are five things it might be," but "I found the cause, I fixed it, and here
is the measurement proving the metric came back."

The moment that convinced us this was worth building was realising what the hard part
actually is. It isn't finding the cause. It's what happens when the agent is **wrong**
— because a real investigation is wrong at least once, and an agent that can't notice
that is worse than useless. It will report success and walk away.

## What it does

You give The Fixer one sentence:

> *"Our conversion rate has dropped significantly today. Find out why and fix the problem."*

Then you leave. The agent surveys conversion across platform, region, traffic source and
app version in parallel to find who is actually affected; reads payment failures, error
logs, deployments, configuration history, feature flags, support tickets and service
health; records competing hypotheses with explicit confidence; applies the smallest
reversible remediation it believes in; waits for real traffic to accumulate; and then
verifies against the actual business metric.

**The moment that matters is the one where it fails.**

In the flagship scenario the agent finds a deployment that shipped 37 minutes before the
symptoms began. Rolling it back is a sound first hypothesis, and the rollback genuinely
succeeds — the tool returns success.

Conversion does not recover.

That failure is not scripted. Runtime configuration is versioned separately from
deployments, exactly as in real systems, so the deployment's release script wrote a config
change that a rollback cannot revert. The agent watches the metric refuse to move, says so
plainly, rejects its own hypothesis, re-investigates, finds the real cause and fixes that.

```
ios conversion   3.61%  →  0.63%        web 4.02%, android 3.46% — untouched
  rollback deployment 8472   →  tool reports success, ios still 0.77%
  restore the configuration  →  ios 3.84%, PAY_CFG_3021 errors → 0, verified
```

Mission Control — the console it runs in — is deliberately not a chat window. It's an
operations display: a live timeline, hypothesis confidence bars with rejected ones struck
through, remediations tagged EFFECTIVE or INEFFECTIVE, and a conversion chart where you
watch one line fall away from the others, stay down through the failed fix, and come back
after the real one.

## How we built it

**Gemini 3.5 Flash through Google ADK 2.7**, running on **Cloud Run**, built by **Cloud
Build**, with inference on **Vertex AI**.

The agent works through 25 structured tools in four kinds — read, act, verify, and a set
of *reasoning* tools that let it record findings, hypotheses, confidence revisions and
conclusions. It never writes SQL and never sees a raw row. Every call routes through one
path that times it, records it, and checks its permissions.

That reasoning layer is what makes the agent's thinking inspectable without exposing raw
chain-of-thought. A rejected hypothesis is a first-class record — visible evidence the
agent changed its mind when the data contradicted it.

The environment, NovaCart, is a simulated e-commerce platform: roughly 450,000 sessions
plus orders, payments, deployments, configuration, feature flags, logs, tickets and
service health. It is a **generator, not a fixture**. An incident plants a *cause*, and
the traffic generator folds that cause's effects into every row it produces. When the
agent changes something, the generator recomputes what is still true and later rows come
out different.

That single design decision is what makes the demo honest: **metric recovery is computed,
never scripted.** A correct fix makes the numbers genuinely recover. A wrong one genuinely
does not.

## Challenges we ran into

**The agent could be credited with a success it never achieved.** Our grader measured
recovery on the aggregate metric. One incident left two regions at a quarter of their
normal conversion — completely unresolved — but those regions are 29% of traffic, so the
overall number stayed within tolerance and passed. We were about to measure precisely the
thing the product exists to prevent. Recovery is now judged on every segment the incident
actually touches.

**Verification produced false negatives.** Comparing one 40-minute window against another
40-minute window from yesterday doubles the sampling noise, and our threshold sat about
1.2 standard deviations away — so a *correct* fix could read as failed and send the agent
chasing a phantom. We widened the reference to a stable three-hour baseline and now score
recovery as the fraction of the observed drop that came back, which is scale-free.

**The simulation wasn't reproducible.** Sim time ran off the wall clock, so the world
depended on how long the code took to execute. Worse, a slow model would have faced a
different scenario than the fast heuristic it was being compared against. There are now
two clock modes: live for the demo, frozen for evaluation.

**A concurrency bug that would have broken the live demo.** The console polls metrics
while the agent works, so the world generator ran from two places at once and collided on
primary keys — throwing 500s into the console mid-mission. Exactly the kind of thing that
surfaces on stage.

## Accomplishments we're proud of

**The failure is real.** We could have scripted the failed rollback. Instead we modelled
configuration as versioned separately from deployments, and the failure falls out of that
as a consequence. Nobody has to be told the first fix didn't work — they watch the line
stay flat.

**Six incidents, five different correct answers.** If every answer were "restore the
config," an agent could score well by pattern-matching the environment instead of
reasoning. One incident is deliberately the mirror image of the flagship: there, rolling
back the deployment *is* the fix. Others are visible only in region, only in funnel stage,
or only in latency.

**We measure the thing that would embarrass us.** The headline metric is *false completion
rate* — claimed success the numbers don't support — reported as a rate we want at zero
rather than an accuracy we want high, so good runs can't average it away. We built a
mission that declares SUCCESS having done nothing, purely to prove the grader catches it.

**The safety gate is a control, not an audit log.** High-risk or irreversible actions
pause the agent mid-action and wait for a human. Verified against the database, not the
agent's report: after a denied refund, zero orders are refunded.

## What we learned

**Tool design is agent design.** More of the agent's quality lives in tool docstrings and
return shapes than in the system prompt. Our verification tools deliberately return no
verdict — only numbers and the sample size behind them. The moment a tool can say
"solved," the agent stops reasoning and starts reading that field out.

**Presenting evidence beats presenting conclusions.** Our parallel survey tool originally
named a single "most uneven dimension." It picked the wrong one for four of six incidents,
because the dimensions overlap — every app version belongs to exactly one platform, so a
platform-wide fault always makes app_version look uneven too, usually more so. We were
doing the agent's reasoning for it, and doing it badly. It now reports a ranking and lets
the agent decide.

**A baseline is what makes a result mean anything.** We built a deterministic heuristic
agent that drives the same tools, the same world and the same mission state — everything
identical except who decides what to do next. "Solved 9 of 10" says little on its own.
"Solved 9 of 10 where a reasonable heuristic solves 3" is a claim about reasoning.

## What's next

Real integrations behind the same tool interface — the tool contracts were designed so the
simulator can be swapped for live analytics and deployment systems without touching the
agent. Then learning across missions, so a cause seen once is recognised faster, and
multi-objective missions where the agent has to decide what to work on first.

---

## Built With

`python` · `google-adk` · `gemini` · `vertex-ai` · `google-cloud-run` · `cloud-build` ·
`cloud-logging` · `fastapi` · `server-sent-events` · `react` · `vite` · `sqlalchemy` ·
`sqlite` · `docker`

---

## Video script outline (≈3:50)

The email says judges weigh the video most, and that Google Cloud proof is required.

| time | on screen | said |
|---|---|---|
| 0:00–0:20 | conversion dashboard, the drop | The problem. "Businesses don't need another AI that tells them something is broken." |
| 0:20–0:35 | typing the objective, click START | The one sentence. Then: "That's the last thing a human does." |
| 0:35–1:30 | live timeline, survey, hypotheses forming | Name **Gemini 3.5 Flash** and **Google ADK** out loud here — the requirement is to say them clearly, not bury them. |
| 1:30–2:15 | rollback executes, chart stays flat | **The moment.** Let it breathe. "The tool succeeded. The problem didn't go away." |
| 2:15–2:50 | hypothesis rejected, real cause found, fix applied, chart recovers | Recovery. |
| 2:50–3:15 | conclusion panel, before/after numbers | Proof, not assertion. |
| 3:15–3:40 | **Cloud Run dashboard + Vertex AI logs + the `.run` URL** | **Required.** Do not skip or shorten this. |
| 3:40–3:50 | — | "The Fixer doesn't tell you what to do. It takes responsibility for getting the problem solved." |

Notes: record at 1600×950 (the console is laid out for it); cut the world-build wait;
upload early, processing can take hours.
