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
| Third tab | Cloud Run → the-fixer → **Logs** (shows Vertex calls) |

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
| 1:15–1:50 | Hypothesis panel + `query_configuration` / `query_deployments` rows | This is where the trap is explained. Hold on the two hypotheses. |
| 1:50–2:20 | `restore_configuration` row, then `wait_for_traffic`, then the chart recovering | **The money shot.** Hold on the chart as the blue line comes back. |
| 2:20–2:45 | Approval dialog | Do not rush. Read the risk fields. Click **REJECT**. |
| 2:45–3:05 | Conclusion panel: PROBLEM SOLVED, root cause, measured numbers | Hold long enough to read. |
| 3:05–3:35 | **Cloud Console**: Cloud Run service page, then Logs showing Vertex calls, then the `.run` URL in the address bar | **Required.** Do not shorten this. |
| 3:35–3:45 | Back to the conclusion panel or a title card | Closing line. |

---

## Narration script

~555 words, about 3:40 at a natural pace. Timings are guides; cut screen
footage to fit the voice, not the other way round.

---

**[0:00 — idle console]**

Every AI operations tool stops in the same place. It notices something is wrong,
explains it clearly, and then hands the problem back to a human. The work of
actually fixing it never moves.

This is The Fixer. It's an autonomous agent that takes an operational objective,
investigates it, fixes it, and then proves the problem is actually gone.

**[0:20 — typing / clicking START MISSION]**

Here's a real incident. Conversion has dropped on an e-commerce platform, and
nobody knows why. The operator gives the agent one sentence — "find out why and
fix the problem" — and clicks start.

That's the last thing a human does.

**[0:35 — timeline filling]**

The agent is running on **Gemini 3.5 Flash**, through the **Google Agent
Development Kit**, on **Cloud Run**, with inference on **Vertex AI**.

Its first move is to find out *who* is affected. It slices conversion by
platform, region, traffic source and app version — all at once, in parallel. The
problem is isolated to iOS. Web and Android are untouched.

It reads the payment failures. An unusual error code, PAY underscore CFG three
oh two one, appearing hundreds of times an hour. Then the error logs, which name
the specific payment profile that's rejecting those transactions.

**[1:15 — hypothesis panel]**

Now it has two competing explanations, and it records both with explicit
confidence.

There's a deployment that shipped thirty-seven minutes before the symptoms
started. That's the obvious suspect — and rolling it back is a real, reversible
action.

But it's the wrong answer. Runtime configuration is versioned separately from
deployments, exactly as in real systems. That deployment's release script changed
a config value, and rolling the deployment back would not revert it.

We measured this. A reasonable heuristic agent falls for that trap in nine runs
out of twelve. Gemini didn't fall for it once.

**[1:50 — restore_configuration, then the chart]**

It goes straight to the configuration change, and restores it.

Then — and this is the part that matters — it waits. A fix only affects traffic
served after it's applied, so the agent lets fifteen minutes of real sessions
accumulate before it judges anything.

Then it verifies. Not "did my tool call return success" — the actual business
metric. iOS conversion, back from zero point six percent to three point five.
Payment errors, down to zero.

**[2:20 — approval dialog]**

Then it finds three hundred and sixty failed orders from the outage and decides
those customers deserve refunds.

It cannot do that on its own. Refunds are high risk and irreversible, so the
agent stops, mid-action, and asks a human. Every tool carries a permission, a
risk level and a reversibility, and the agent physically cannot execute this one
without approval.

**[2:45 — conclusion panel]**

Mission complete. Root cause, the evidence for it, and the measured before and
after.

Across twelve missions on six different incidents, the agent identified the root
cause every time, applied the correct fix every time, and the metric genuinely
recovered every time. Zero unauthorised actions. And a false completion rate of
zero — it never once claimed a success the numbers didn't support.

**[3:05 — Cloud Console]**

All of this runs on Google Cloud. Cloud Run hosting the service, Vertex AI
serving Gemini 3.5 Flash, Cloud Build producing the container, and Cloud Logging
recording every tool call and every refusal.

**[3:35 — close]**

The Fixer doesn't tell you what to do. It takes responsibility for getting the
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
> - Pause briefly before "But it's the wrong answer."
> - Slow down for "zero point six percent to three point five".
> - Slight emphasis on "It cannot do that on its own."
> - The final line is quieter and slower than everything before it.
>
> **Avoid:** exclamation, upward inflection at line ends, over-articulating the
> error code — read "PAY CFG three oh two one" as a technician would say it.

### Retiming

If the generated audio runs long, cut these first — they carry the least:

1. "Web and Android are untouched." (0:35 block)
2. "Cloud Build producing the container" (3:05 block)
3. "the evidence for it" (2:45 block)

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
