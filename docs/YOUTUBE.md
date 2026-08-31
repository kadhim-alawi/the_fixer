# YouTube upload — paste-ready fields

---

## Title

**Use this:**

```
The Fixer — Autonomous Incident Resolution with Gemini 3.5 Flash + Google ADK
```

76 characters, well under the 100 limit. It leads with the project name, says
what it is, and names both required technologies where a judge scanning a list
will see them.

Alternatives if you prefer:

```
The Fixer: Give It a Problem, Walk Away — Gemini 3.5 Flash + Google ADK on Cloud Run
```

```
The Fixer — an AI agent that fixes production incidents and proves it (Gemini 3.5 + ADK)
```

---

## Description

Paste everything between the lines. **Check the chapter timestamps against your
final edit before publishing** — if they do not match, YouTube will still accept
them but they will jump to the wrong place.

---

Today's AI tells you what's broken. The Fixer investigates, fixes it, and proves it's fixed — against real business metrics, not its own say-so.

Built for the All Things Agentic Hackathon (Taskmaster track).

An operator gives the agent one sentence — "our conversion rate has dropped, find out why and fix the problem" — and walks away. The agent surveys the data to find who is affected, reads payment failures, error logs, deployments and configuration history, forms a hypothesis, applies a fix, waits for real traffic to accumulate, and then verifies against the actual business metric. A tool returning success is not treated as evidence the problem is solved.

In this run it finds a payments configuration key that a deployment's release script silently changed, restores it, and confirms iOS conversion recovering from 0.638% to 3.533% with the error count dropping to zero. Then it tries to refund the 469 orders lost during the outage — and stops, because refunds are irreversible and require a human.

▶ TRY IT LIVE (no login)
https://the-fixer-366816219932.us-central1.run.app

▶ SOURCE CODE
https://github.com/kadhim-alawi/the_fixer

── BUILT WITH ──────────────────────────
• Gemini 3.5 Flash, served via Vertex AI
• Google Agent Development Kit (ADK) 2.7, Python
• Cloud Run — hosting
• Cloud Build — container build
• Cloud Logging — request and mission logs
• FastAPI + Server-Sent Events, React

── MEASURED RESULTS ────────────────────
Twelve missions across six different incident types, scored by a grader that
sits outside the agent and reads the environment directly:

  root cause identified ......... 100%
  correct remediation applied ... 100%
  metric genuinely recovered .... 100%
  false completion .............. 0%
  unauthorised actions .......... 0

A deterministic heuristic agent, run on identical seeds through the identical
grader, scores 50% and needs a second attempt in 9 of 12 runs. That comparison
is the point: it makes the result a claim about reasoning rather than about the
environment being easy.

── HOW IT IS KEPT HONEST ───────────────
The environment is a simulated e-commerce platform, and deliberately so — an
agent taking real remediations against real production cannot be evaluated. You
cannot run the same incident two hundred times, and you cannot know the ground
truth. What is NOT simulated: the agent, the reasoning, the tool calls, the
failures, and the metrics it is judged on.

Recovery is computed, never scripted. An incident plants a cause; the traffic
generator folds that cause's effects into every row it produces. When the agent
changes something, later rows come out different. A correct fix makes the
numbers genuinely recover. A wrong one genuinely does not.

── CHAPTERS ────────────────────────────
0:00 The problem
0:20 One sentence, then walk away
0:35 Investigating — Gemini 3.5 Flash on Google ADK
1:15 The trap: the obvious suspect is the wrong answer
1:50 The fix, and waiting for real traffic
2:20 Approval required — the safety gate
2:45 Verified: what actually changed
3:05 Running on Google Cloud

#GoogleCloud #Gemini #AIAgents #VertexAI #CloudRun #Hackathon

---

## Tags

Paste into the Tags field, comma separated:

```
the fixer, ai agent, autonomous agent, gemini, gemini 3.5 flash, google adk, agent development kit, vertex ai, google cloud, cloud run, agentic ai, ai ops, incident response, root cause analysis, devpost, hackathon, all things agentic
```

---

## Settings checklist

| field | set to | why |
|---|---|---|
| **Visibility** | **Public** | The rules require publicly visible. Unlisted does not qualify. |
| Category | Science & Technology | |
| Audience | **No, not made for kids** | Kids-directed videos disable features and look wrong here |
| Age restriction | No | |
| Language | English | |
| Subtitles | Upload an SRT, or burn them in | Required if narration is not clearly English. Recommended regardless — judges may watch muted |
| Comments | Leave on | Nothing to hide |
| Playlist | Not needed | |
| Thumbnail | See below | |
| Altered content | See note below | |

### Altered/synthetic content question

YouTube asks whether the video contains synthetic content that could mislead —
it is aimed at realistic depictions of real people saying things they did not
say. An AI narrator over a genuine screen recording does not meet that bar, so
**No** is a correct answer.

If you would rather be explicit, answer No and add a line at the end of the
description: *"Narration is an AI voiceover. All screen footage is an unedited
recording of the live application."* That is honest and costs nothing.

### Thumbnail

Easiest good option: a still from the moment the chart has recovered, with the
green EFFECTIVE tag and the conclusion panel visible. Grab it from the footage
at around 2:50.

If you want text on it, keep it to three or four words — **"It fixes it."** or
**"Give it a problem."** — large, top-left, so it survives being shown small.

---

## After publishing

1. Open the link in an **incognito window**. If it does not play, it is not
   public and the submission will fail.
2. Confirm the runtime is **under 4:00**. Only the first four minutes are judged
   and going over risks the whole thing.
3. Watch it once, all the way through, muted. Anything unreadable at small size
   needs fixing now, not after submission.
4. Paste the link into the Devpost form.
5. **Then stop touching everything** — repo, video and links are frozen once the
   deadline passes.
