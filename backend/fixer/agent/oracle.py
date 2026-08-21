"""A deterministic agent, for testing and for comparison.

This is not a mock. It drives the real tools, the real world and the real
Mission object over the same code path the LLM agent uses. What it replaces is
only the decision-making: instead of a model choosing what to do next, a small
set of heuristics does.

It exists for two reasons.

**It tests the pipeline without a model.** Mission state, the evidence ledger,
verification, the failure-and-recovery arc and grading can all be exercised and
regression-tested with no credentials, no latency and no cost.

**It is the baseline the LLM agent is measured against.** "Our agent solved 9
of 10 incidents" means little on its own. "Our agent solved 9 of 10 where a
reasonable heuristic solves 4" is a claim about the model actually reasoning.

The heuristics are deliberately shallow -- segment the metric, look at the
loudest error, suspect the most recent deployment, then suspect a recent
configuration change. That is roughly what an experienced operator would try
first, including the part where the first attempt is wrong.
"""

from __future__ import annotations

import re
from typing import AsyncIterator

from .. import tools as T
from ..mission import HypothesisState, Mission, MissionStatus
from ..sim.world import World
from .runner import MissionEvent, MissionResult

# Error codes that occur constantly on healthy traffic and mean nothing on their own.
ORDINARY = {"CARD_DECLINED", "INSUFFICIENT_FUNDS", "EXPIRED_CARD", "RISK_HOLD"}

# Long enough for a usable sample at NovaCart's traffic levels.
SETTLE_MINUTES = 65
VERIFY_WINDOW = 60


async def run_oracle(
    world: World,
    objective: str,
    *,
    mission: Mission | None = None,
    fast_forward: bool = True,
    guard=None,
) -> AsyncIterator[tuple[MissionEvent, Mission]]:
    """Run the heuristic agent, yielding events as it goes.

    Accepts a caller-supplied Mission so that whatever is watching the run --
    Mission Control, the evaluation harness -- holds the same object the agent
    is writing into, rather than a copy that never updates.
    """
    mission = mission or Mission(
        objective=objective,
        success_criteria=[
            "root cause identified",
            "remediation applied",
            "objective metric recovered and verified",
        ],
    )
    env = T.ToolEnv(world=world, mission=mission, guard=guard, fast_forward=fast_forward)
    T.set_env(env)

    seq = 0

    def emit(kind: str, text: str, **kw) -> MissionEvent:
        nonlocal seq
        seq += 1
        return MissionEvent(
            seq=seq, kind=kind, text=text,
            sim_time=world.now().isoformat(timespec="seconds"), **kw
        )

    yield emit("message", f"Mission accepted: {objective}"), mission

    # -- 1. Is there actually a problem, and who has it? --------------------
    overall = await T.verify.check_conversion(window_minutes=60, platform="all")
    yield emit(
        "tool_result",
        f"overall conversion {overall['current']['rate_pct']}% vs "
        f"{overall['reference_24h_ago']['rate_pct']}% yesterday "
        f"({overall['change']['relative_pct']}%)",
        tool="check_conversion",
    ), mission

    funnel = await T.read.query_conversion_funnel(window_minutes=60, split_by="platform")
    segments = [s for s in funnel.get("segments", []) if s["sessions"] > 300]
    if not segments:
        mission.conclude(
            MissionStatus.INSUFFICIENT_EVIDENCE, "", "Not enough traffic to segment.", "",
            world.now().isoformat(timespec="seconds"),
        )
        yield emit("message", "Insufficient traffic to investigate."), mission
        return

    worst = min(segments, key=lambda s: s["conversion_rate_pct"] or 0)
    healthy = [s for s in segments if s is not worst]
    healthy_avg = sum(s["conversion_rate_pct"] or 0 for s in healthy) / max(1, len(healthy))
    isolated = (worst["conversion_rate_pct"] or 0) < healthy_avg * 0.6
    platform = worst["platform"]

    await T.reason.record_finding(
        summary=(
            f"{platform} conversion is {worst['conversion_rate_pct']}% against "
            f"{healthy_avg:.2f}% on other platforms"
            if isolated
            else "conversion is down across all platforms"
        ),
        detail=f"checkout completion {platform}={worst['checkout_completion_pct']}%, "
        f"sessions={worst['sessions']}",
    )
    yield emit(
        "thought",
        f"Problem is {'isolated to ' + platform if isolated else 'platform-wide'}.",
    ), mission

    # -- 2. What does the failure look like? --------------------------------
    pay = await T.read.query_payments(window_minutes=60, split_by="platform")
    unusual = [
        c for c in pay["failures_by_error_code"]
        if c["error_code"] not in ORDINARY and c["count"] > 5
    ]
    signature = unusual[0]["error_code"] if unusual else None
    if signature:
        await T.reason.record_finding(
            summary=f"payment error {signature} occurring {unusual[0]['count']} times/hour",
            detail=f"not an ordinary decline code; failure_rate={pay['failure_rate_pct']}%",
        )
        yield emit("thought", f"Unusual payment failure signature: {signature}"), mission

    logs = await T.read.query_logs(
        window_minutes=60, level="ERROR", platform=platform if isolated else "", limit=5
    )
    log_text = " ".join(s["message"] for s in logs.get("samples", []))
    # Log lines name the component or setting involved; pull out quoted tokens.
    named = re.findall(r"'([a-z0-9_]+)'", log_text)
    if named:
        await T.reason.record_finding(
            summary=f"error logs name {named[0]!r}",
            detail=(log_text[:200] + "...") if len(log_text) > 200 else log_text,
        )
        yield emit("thought", f"Logs implicate {named[0]!r}"), mission

    # -- 3. What changed? ---------------------------------------------------
    deploys = await T.read.query_deployments(hours_back=6)
    recent = deploys.get("deployments", [])
    top_deploy = recent[0] if recent else None

    cfg = await T.read.query_configuration(changed_within_hours=24)
    changed = [c for c in cfg["entries"] if c["changed"]]
    # Prefer a changed key that mentions the affected segment, then one whose new
    # value appears in the error logs.
    candidates = [c for c in changed if isolated and platform in c["key"]]
    if not candidates and named:
        candidates = [c for c in changed if c["value"] in named]
    if not candidates:
        candidates = changed
    top_config = candidates[0] if candidates else None

    if top_deploy:
        h1 = mission.add_hypothesis(
            f"deployment {top_deploy['ref']} ({top_deploy['service']}) introduced a "
            f"regression {top_deploy['minutes_ago']} minutes before the symptom",
            0.65,
        )
        yield emit("thought", f"H1 (0.65): deployment {top_deploy['ref']}"), mission
    else:
        h1 = None

    if top_config:
        h2 = mission.add_hypothesis(
            f"configuration {top_config['key']} changed "
            f"{top_config['previous_value']} -> {top_config['value']} "
            f"by {top_config['updated_by']}",
            0.45,
        )
        yield emit("thought", f"H2 (0.45): config {top_config['key']}"), mission
    else:
        h2 = None

    # -- 4. Act on the leading hypothesis -----------------------------------
    # The most recent deployment is the conventional first suspect. Here that is
    # the wrong answer, and finding that out is the point.
    async def settle_and_check() -> tuple[bool, dict]:
        await T.verify.wait_for_traffic(minutes=SETTLE_MINUTES)
        chk = await T.verify.check_conversion(
            window_minutes=VERIFY_WINDOW, platform=platform if isolated else "all"
        )
        cur = chk["current"]["rate_pct"] or 0
        ref = chk["reference_24h_ago"]["rate_pct"] or 0
        return (chk["sufficient_sample"] and ref > 0 and cur >= ref * 0.85), chk

    if h1 and top_deploy:
        yield emit(
            "action",
            f"rollback_deployment({top_deploy['ref']})",
            tool="rollback_deployment",
            risk="MEDIUM",
        ), mission
        await T.act.rollback_deployment(
            deployment_ref=top_deploy["ref"],
            reason=f"most recent change to a service on the affected path; "
            f"deployed {top_deploy['minutes_ago']}m before the symptom",
        )
        ok, chk = await settle_and_check()
        yield emit(
            "tool_result",
            f"after rollback: {chk['current']['rate_pct']}% vs {chk['reference_24h_ago']['rate_pct']}% reference",
            tool="check_conversion",
        ), mission

        await T.reason.assess_remediation(
            effective=ok,
            metric_summary=f"{platform} conversion {chk['current']['rate_pct']}% "
            f"vs reference {chk['reference_24h_ago']['rate_pct']}%",
            note="rollback resolved the incident" if ok else
            "rollback did not restore conversion; the cause is elsewhere",
        )
        await T.reason.revise_hypothesis(
            hypothesis_id=h1.id,
            confidence=0.9 if ok else 0.05,
            reason="metric recovered after rollback" if ok else
            "metric unchanged after rollback, so the deployment was not the cause",
            state="confirmed" if ok else "rejected",
        )
        if not ok:
            yield emit(
                "thought",
                "Rollback did not restore conversion. H1 rejected -- revising.",
            ), mission
            if h2:
                await T.reason.revise_hypothesis(
                    hypothesis_id=h2.id,
                    confidence=0.85,
                    reason="deployment ruled out; configuration persists independently "
                    "of deployments and was changed in the same window",
                )
        if ok:
            await _finish(mission, world, h1.statement, chk, platform)
            yield emit("message", "MISSION COMPLETE"), mission
            return

    # -- 5. Recover: act on the revised leading hypothesis ------------------
    if not (h2 and top_config):
        mission.conclude(
            MissionStatus.INSUFFICIENT_EVIDENCE, "",
            "No further candidate cause identified.", "",
            world.now().isoformat(timespec="seconds"),
        )
        yield emit("message", "Exhausted candidates without resolving."), mission
        return

    yield emit(
        "action",
        f"restore_configuration({top_config['key']})",
        tool="restore_configuration",
        risk="LOW",
    ), mission
    await T.act.restore_configuration(
        key=top_config["key"],
        reason=f"changed from {top_config['previous_value']} to {top_config['value']} "
        f"by {top_config['updated_by']} in the incident window; deployment rollback "
        f"did not revert it",
    )
    ok, chk = await settle_and_check()
    yield emit(
        "tool_result",
        f"after config restore: {chk['current']['rate_pct']}% vs "
        f"{chk['reference_24h_ago']['rate_pct']}% reference",
        tool="check_conversion",
    ), mission

    await T.reason.assess_remediation(
        effective=ok,
        metric_summary=f"{platform} conversion {chk['current']['rate_pct']}% "
        f"vs reference {chk['reference_24h_ago']['rate_pct']}%",
        note="configuration restore resolved the incident" if ok else
        "configuration restore did not resolve the incident",
    )
    await T.reason.revise_hypothesis(
        hypothesis_id=h2.id,
        confidence=0.95 if ok else 0.1,
        reason="metric recovered after restoring the configuration" if ok else
        "metric still depressed",
        state="confirmed" if ok else "rejected",
    )

    if ok:
        await _finish(mission, world, h2.statement, chk, platform)
        yield emit("message", "MISSION COMPLETE"), mission
    else:
        mission.conclude(
            MissionStatus.FAILED, "",
            "Both candidate causes were ruled out by verification.", "",
            world.now().isoformat(timespec="seconds"),
        )
        yield emit("message", "Unable to resolve; escalating."), mission


async def _finish(mission: Mission, world: World, root_cause: str, chk: dict, platform: str) -> None:
    err = await T.verify.check_error_rate(window_minutes=VERIFY_WINDOW, error_code="")
    await T.reason.conclude_mission(
        status="SUCCESS",
        root_cause=root_cause,
        evidence_summary="; ".join(f.summary for f in mission.findings),
        before_after=f"{platform} conversion {chk['current']['rate_pct']}% "
        f"(reference {chk['reference_24h_ago']['rate_pct']}%), "
        f"failed payments now {err['current_count']}",
    )


async def run_oracle_mission(
    world: World, objective: str, *, fast_forward: bool = True, guard=None
) -> tuple[MissionResult, Mission]:
    """Convenience wrapper that collects the whole run."""
    result = MissionResult(mission_id="oracle", objective=objective)
    mission: Mission | None = None
    async for ev, m in run_oracle(world, objective, fast_forward=fast_forward, guard=guard):
        result.events.append(ev)
        mission = m
    assert mission is not None
    result.tool_calls = T.get_env().calls
    result.final_text = mission.conclusion.root_cause if mission.conclusion else ""
    return result, mission
