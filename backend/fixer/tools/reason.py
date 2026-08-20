"""Reasoning tools.

These are how the agent's thinking becomes *data* rather than prose buried in a
transcript. The agent states what it found, what it thinks is going on, how
confident it is, and why that confidence moved.

This is deliberately not chain-of-thought exposure. The agent is not narrating
its internal reasoning; it is recording conclusions it is willing to stand
behind, in a structure that Mission Control can render and the grader can
score. A hypothesis that gets rejected is as valuable a record as one that gets
confirmed -- it is the visible evidence that the agent actually changed its mind
when the data said so.
"""

from __future__ import annotations

from ..mission import HypothesisState, MissionStatus
from .base import get_env, invoke, tool


def _mission():
    m = get_env().mission
    if m is None:
        raise RuntimeError("No mission bound to this tool environment.")
    return m


@tool(kind="reason", permission="READ")
async def record_finding(summary: str, detail: str) -> dict:
    """Record something you established from the data.

    Use this for each substantive thing you learn, as you learn it -- which
    segment is affected, what the error signature is, what changed and when.
    These become the mission's evidence trail.

    Args:
        summary: One line, specific and quantified. Prefer "iOS checkout
            completion is 8.8% against 42.9% on web" over "iOS looks bad".
        detail: The numbers or log text this rests on, and which tool produced it.

    Returns:
        The finding's sequence number and the running evidence count.
    """

    async def body() -> dict:
        m, env = _mission(), get_env()
        f = m.add_finding(summary, detail, env.world.now().isoformat(timespec="seconds"))
        return {"finding_seq": f.seq, "total_findings": len(m.findings)}

    return await invoke("record_finding", {"summary": summary, "detail": detail}, body)


@tool(kind="reason", permission="READ")
async def record_hypothesis(statement: str, confidence: float) -> dict:
    """Put forward an explanation for the problem, with your confidence in it.

    Record more than one. Competing explanations are how an investigation stays
    honest -- a single hypothesis held from the start tends to collect only the
    evidence that supports it.

    A good statement names a specific mechanism, not a general area. Prefer
    "config key X was changed to Y, which rejects Z requests" over "something
    wrong with payments".

    Args:
        statement: The proposed cause and the mechanism connecting it to the symptom.
        confidence: How likely you think this is, from 0.0 to 1.0.

    Returns:
        The hypothesis id to use when revising it later, and all live hypotheses.
    """

    async def body() -> dict:
        m = _mission()
        h = m.add_hypothesis(statement, confidence)
        return {
            "hypothesis_id": h.id,
            "current": [x.as_dict() for x in m.hypotheses],
        }

    return await invoke(
        "record_hypothesis", {"statement": statement, "confidence": confidence}, body
    )


@tool(kind="reason", permission="READ")
async def revise_hypothesis(
    hypothesis_id: str, confidence: float, reason: str, state: str = "open"
) -> dict:
    """Move your confidence in a hypothesis as evidence arrives.

    Call this whenever something you learn makes an explanation more or less
    likely, including when a remediation you based on it fails to help.

    Args:
        hypothesis_id: The id returned by record_hypothesis, for example "H1".
        confidence: Your revised confidence, from 0.0 to 1.0.
        reason: The specific evidence that moved it.
        state: "open" to keep considering it, "rejected" if it is ruled out,
            "confirmed" if the evidence establishes it.

    Returns:
        The updated hypothesis and all live hypotheses.
    """

    async def body() -> dict:
        m = _mission()
        h = m.hypothesis(hypothesis_id)
        if h is None:
            return {
                "error": "unknown_hypothesis",
                "known_ids": [x.id for x in m.hypotheses],
            }
        try:
            new_state = HypothesisState(state.lower())
        except ValueError:
            new_state = HypothesisState.OPEN
        h.revise(confidence, reason, new_state)
        return {"updated": h.as_dict(), "current": [x.as_dict() for x in m.hypotheses]}

    return await invoke(
        "revise_hypothesis",
        {"hypothesis_id": hypothesis_id, "confidence": confidence, "reason": reason, "state": state},
        body,
    )


@tool(kind="reason", permission="READ")
async def assess_remediation(effective: bool, metric_summary: str, note: str) -> dict:
    """State whether your most recent remediation actually fixed the problem.

    Call this after verifying against real metrics. Judge the objective's
    metric, not whether the action tool reported success.

    Answering `false` here is a normal and expected outcome. It records that an
    approach was tried and did not work, which is what stops you repeating it
    and what tells you the cause is elsewhere.

    Args:
        effective: True only if the metric named in the objective has actually
            recovered, on a sufficient sample.
        metric_summary: The numbers, for example "ios conversion 0.7% -> 3.6%,
            reference 3.6%, 4,100 sessions".
        note: What this means for your hypotheses and what you will do next.

    Returns:
        Which action was assessed and the mission's running counters.
    """

    async def body() -> dict:
        m, env = _mission(), get_env()
        v = m.record_verification(
            metric="objective_metric",
            current=None,
            reference=None,
            change_pct=None,
            sufficient_sample=True,
            agent_verdict="recovered" if effective else "not_recovered",
            note=f"{metric_summary} | {note}",
            sim_time=env.world.now().isoformat(timespec="seconds"),
        )
        assessed = next((a for a in reversed(m.actions) if a.verification_note), None)
        return {
            "verification_seq": v.seq,
            "assessed_action": assessed.tool if assessed else None,
            "failed_remediations": len(m.failed_actions),
            "recovered_after_failure": m.recovered_after_failure,
        }

    return await invoke(
        "assess_remediation",
        {"effective": effective, "metric_summary": metric_summary, "note": note},
        body,
    )


@tool(kind="reason", permission="READ")
async def conclude_mission(
    status: str, root_cause: str, evidence_summary: str, before_after: str
) -> dict:
    """End the mission and state your result.

    Only use status "SUCCESS" when the objective's metric has actually recovered
    and you have verified it. If you cannot get there, end with the status that
    describes why -- an honest FAILED or REQUIRES_HUMAN is a legitimate outcome,
    and a SUCCESS that the numbers do not support is not.

    Args:
        status: One of SUCCESS, FAILED, BLOCKED, REQUIRES_HUMAN,
            INSUFFICIENT_EVIDENCE, SAFETY_LIMIT.
        root_cause: What was actually wrong, specifically.
        evidence_summary: The evidence establishing that, in a few lines.
        before_after: The measured numbers before and after your remediation.

    Returns:
        The recorded conclusion.
    """

    async def body() -> dict:
        m, env = _mission(), get_env()
        try:
            st = MissionStatus(status.upper())
        except ValueError:
            st = MissionStatus.FAILED
        if st is MissionStatus.RUNNING:
            st = MissionStatus.FAILED
        c = m.conclude(
            st, root_cause, evidence_summary, before_after,
            env.world.now().isoformat(timespec="seconds"),
        )
        return {"concluded": c.as_dict(), "counters": m.as_dict()["counters"]}

    return await invoke(
        "conclude_mission",
        {
            "status": status,
            "root_cause": root_cause,
            "evidence_summary": evidence_summary,
            "before_after": before_after,
        },
        body,
    )
