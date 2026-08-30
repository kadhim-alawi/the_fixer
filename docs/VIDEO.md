# Demo video — script, shot list and AI voiceover prompt

Target length **3:45**. The cap is 4:00 and only the first four minutes are
judged, so leave headroom.

**The footage must be a real screen recording.** The rules require the agent to
be seen actually working — real logs, no mockups — and require visible Google
Cloud proof. Generate the *voice*, not the video.

---

## Before you record

| | |
|---|---|
| Browser window | 1600 × 950, no bookmarks bar, no extensions visible |
| URL | https://the-fixer-366816219932.us-central1.run.app |
| Scenario | `payment config regression` (the default) |
| Second tab | Google Cloud console → Cloud Run → `the-fixer` service |
| Third tab | Cloud Run → the-fixer → **Logs**, and the **Metrics** tab |

Do one full practice run first so you know the pacing. A mission takes roughly
three to four minutes of real time; you will speed parts of it up in editing.

**Record the whole mission in one take**, then cut. Do not try to record in
segments — the timeline has to be continuous to be believable.

---

## Shot list

| time | screen | what is happening |
|---|---|---|
| 0:00–0:20 | Mission Control, idle, objective visible | Opening. Nothing moving yet. |
| 0:20–0:35 | Click **START MISSION** | The only human action in the whole video. |
| 0:35–1:15 | Timeline filling: `survey_segments`, findings, hypotheses | Speed to ~1.5× if slow. Let the tool names be readable. |
| 1:15–1:50 | `query_deployments` then `query_configuration` rows, hypothesis panel | The trap. Hold on the deployment row, then on H1 reaching 100%. |
| 1:50–2:20 | `restore_configuration` row, then `wait_for_traffic`, then the chart recovering | **The money shot.** Hold on the chart as the blue line comes back. |
| 2:20–2:45 | Approval dialog | Do not rush. Read the risk fields. Click **REJECT**. |
| 2:45–3:05 | Conclusion panel: PROBLEM SOLVED, root cause, measured numbers | Hold long enough to read. |
| 3:05–3:35 | **Cloud Console**: the `the-fixer` service page with region and `.run` URL visible, then the Logs tab, then Metrics | **Required.** Do not shorten this. |
| 3:35–3:45 | Back to the conclusion panel or a title card | Closing line. |

---

## Narration script

**Matched to the recorded footage of mission M-C74B6D6D.** Every number spoken
here appears on screen in that take:

| spoken | where it appears on screen |
|---|---|
| 221 payment failures | `record_finding` at 14:54:25, and again in the conclusion panel |
| 806 log entries ("more than eight hundred") | `query_logs` result at 15:03:26 |
| 30 minutes / 8,760 sessions | `wait_for_traffic` and its result at 16:04:37 |
| 94% payment success | `check_payment_success` at 16:15:33 |
| 469 failed orders | `query_orders` at 16:33:53 |
| 0.638% → 3.533%, reference 3.517% | conclusion panel, "measured" |

Careful with one thing: later in the timeline `PAY_CFG_3021 x381` appears — that
is the same error over a 180-minute window, not a contradiction of the 221 over
60 minutes. Do not cut those two shots adjacent to each other.
 If you re-record, re-check them — a figure
in the voiceover that contradicts the console is worse than no figure at all.

~550 words, about 3:40 at a natural pace.

---

**[0:00 — idle console]**

Every AI operations tool stops in the same place. It notices something is wrong,
explains it clearly, and hands the problem back to a human. The work of actually
fixing it never moves.

This is The Fixer. An autonomous agent that takes an operational objective,
investigates it, fixes it, and then proves the problem is actually gone.

**[0:20 — clicking START MISSION]**

Conversion has dropped on an e-commerce platform, and nobody knows why. The
operator gives the agent one sentence — find out why, and fix the problem — and
clicks start.

That is the last thing a human does.

**[0:35 — survey_segments and the first findings]**

The agent runs on **Gemini 3.5 Flash**, through the **Google Agent Development
Kit**, on **Cloud Run**, with inference on **Vertex AI**.

Its first move is to find out *who* is affected. It slices conversion by
platform, region, traffic source and app version — six queries, run
concurrently. iOS is severely impacted. Web and Android are untouched.

Then the failure signature. Two hundred and twenty-one payment failures in the
last hour, all carrying the same unusual error code. More than eight hundred
error log entries from the payments service, naming the exact provider profile
that is rejecting those transactions.

**[1:15 — query_deployments, then query_configuration]**

This is where most investigations go wrong.

There is a deployment on the board, and a deployment shortly before an incident
is the obvious suspect. Rolling it back is the first thing most people would try.

It would not have worked. Configuration is versioned separately from code, as it
is in real systems — so rolling that deployment back would not have reverted the
setting its own release script changed.

Watch what the agent does. It looks at the deployments, then goes to the
configuration history, and finds the key that release script changed at thirteen
hundred UTC. It never proposes the rollback at all. Confidence: one hundred
percent.

**[1:50 — restore_configuration, wait, then the chart recovering]**

It restores the configuration.

Then it waits. A fix only affects traffic served after it is applied, so the
agent lets thirty minutes of real sessions accumulate — eight thousand seven
hundred and sixty of them — before it judges anything.

Then it verifies against the actual business metric, not whether its own tool
call returned success. iOS payment success rate: ninety-four percent. Failures
with that error code: zero.

**[2:20 — approval dialog]**

Then it looks at the damage. Four hundred and sixty-nine orders failed during
the outage, and the agent decides those customers should be refunded.

It cannot do that on its own. Every tool carries a permission, a risk level and
a reversibility — and refunds are high risk and irreversible. So the agent stops
mid-action and puts the decision in front of a human.

**[2:45 — conclusion panel]**

Mission complete — root cause, evidence, and the measured before and after. iOS
conversion was zero point six four percent during the incident. Afterwards,
three point five three, against a reference of three point five two.

Across twelve missions on six different incidents, the agent found the root
cause every time, applied the correct fix every time, and the metric genuinely
recovered every time. Zero unauthorised actions. And a false completion rate of
zero — it never once claimed a success the numbers did not support.

**[3:05 — Cloud Console]**

All of this runs on Google Cloud. Cloud Run hosting the service in
us-central-one, Vertex AI serving Gemini 3.5 Flash, and the live request logs
and metrics from the mission you just watched.

**[3:35 — close]**

The Fixer does not tell you what to do. It takes responsibility for getting the
problem solved — and it proves it.

---

## AI voiceover prompt

Paste into ElevenLabs, PlayHT, or similar. Generate section by section so you
can retime individual parts against the footage.

> **Voice:** Male or female, 30s–40s, neutral British or American accent.
> Think technical documentary narrator, not advertisement.
>
> **Delivery:** Measured and calm. This is a demonstration of something working,
> not a sales pitch. No rising enthusiasm, no vocal fry, no "sizzle". Confident
> and slightly understated — the numbers do the persuading.
>
> **Pace:** ~150 words per minute. Slower on the numbers.
>
> **Emphasis:**
> - Land clearly on the product names: *Gemini 3.5 Flash*, *Google Agent
>   Development Kit*, *Cloud Run*, *Vertex AI*.
> - Pause briefly before "It would not have worked."
> - Land "It never proposes the rollback at all" flatly, without triumph.
> - Slow down for "zero point six four percent" and "three point five three".
> - Slight emphasis on "It cannot do that on its own."
> - The final line is quieter and slower than everything before it.
>
> **Avoid:** exclamation, upward inflection at line ends, over-articulating the
> error code — read "PAY CFG three oh two one" as a technician would say it.

### Retiming

If the generated audio runs long, cut these first — they carry the least:

1. "Web and Android are untouched." (0:35 block)
2. "and the live request logs and metrics from the mission you just watched" (3:05)
3. "root cause, evidence, and" (2:45 block)

Do **not** cut: any product name, the false-completion sentence, or the Cloud
Cloud proof section.

---

## Editing notes

- **Cut every wait.** The world build, the `wait_for_traffic` pauses, and any
  loading. Speed the investigation section to 1.5–2× so tool names stay readable
  while the timeline moves.
- **Subtitles.** Burn them in, or upload an SRT. Required if the narration is not
  clearly in English.
- **No music, or very quiet.** Narration over a screen recording reads as
  competent; a soundtrack reads as marketing.
- **Export 1080p.** The console is dense and text must stay legible.
- **Upload early.** YouTube processing can take hours. Set it **public**, not
  unlisted — the rules require publicly visible.
