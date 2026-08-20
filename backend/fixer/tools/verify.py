"""Verification tools.

These answer one question: *did the numbers actually move?*

They compare a recent window against the same window 24 hours earlier, which is
the comparison an operator would make and which reveals nothing about when any
incident began. They deliberately return no verdict -- no field here says
"solved". Deciding whether the objective is met is the verifier agent's job,
and it must make that call from the numbers and the sample sizes.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import Integer, and_, case, func, select

from ..sim.schema import Payment, Session
from .base import get_env, invoke, pct, tool, window

# Below this many sessions a rate is too noisy to conclude anything from.
MIN_SAMPLE = 400

# The reference is deliberately wider than the window being tested. A single
# short slice from yesterday carries as much sampling noise as the measurement
# itself, and comparing one noisy number against another produces false
# "did not recover" verdicts -- which would send the agent chasing a phantom
# failure after a fix that actually worked.
REFERENCE_MINUTES = 180


def _delta(current: float | None, reference: float | None) -> dict:
    if current is None or reference is None:
        return {"absolute": None, "relative_pct": None}
    return {
        "absolute": round(current - reference, 3),
        "relative_pct": round(100.0 * (current - reference) / reference, 1)
        if reference
        else None,
    }


@tool(kind="verify", permission="READ")
async def check_conversion(window_minutes: int = 30, platform: str = "all") -> dict:
    """Conversion rate now versus the same window 24 hours ago.

    Args:
        window_minutes: Length of the window to measure, in minutes. Shorter
            windows react faster but carry more sampling noise.
        platform: "all", "web", "ios" or "android".

    The reference is measured over a wider window than the current one (at
    least three hours), because a short slice from yesterday is too noisy to
    compare against reliably.

    Returns:
        Current and reference conversion rates with the session counts behind
        them, and the change between them. `sufficient_sample` is false when
        the window is too thin to draw a conclusion from -- widen the window
        or wait rather than treating a thin result as a real movement.
    """

    async def body() -> dict:
        w = get_env().world
        cur_start, cur_end = window(w, window_minutes, 0)
        ref_start, ref_end = window(w, max(window_minutes, REFERENCE_MINUTES), 24 * 60)

        async def rate(start, end):
            conds = [Session.ts >= start, Session.ts < end]
            if platform != "all":
                conds.append(Session.platform == platform)
            async with w.sf() as s:
                total, conv = (
                    await s.execute(
                        select(
                            func.count(Session.id),
                            func.sum(func.cast(Session.converted, Integer)),
                        ).where(and_(*conds))
                    )
                ).one()
            return (total or 0), (conv or 0)

        cur_n, cur_c = await rate(cur_start, cur_end)
        ref_n, ref_c = await rate(ref_start, ref_end)
        cur_rate, ref_rate = pct(cur_c, cur_n), pct(ref_c, ref_n)

        return {
            "metric": "conversion_rate_pct",
            "platform": platform,
            "window_minutes": window_minutes,
            "current": {"rate_pct": cur_rate, "sessions": cur_n, "conversions": cur_c},
            "reference_24h_ago": {
                "rate_pct": ref_rate, "sessions": ref_n, "conversions": ref_c,
                "window_minutes": max(window_minutes, REFERENCE_MINUTES),
            },
            "change": _delta(cur_rate, ref_rate),
            "sufficient_sample": cur_n >= MIN_SAMPLE and ref_n >= MIN_SAMPLE,
            "min_sample_for_confidence": MIN_SAMPLE,
        }

    return await invoke(
        "check_conversion", {"window_minutes": window_minutes, "platform": platform}, body
    )


@tool(kind="verify", permission="READ")
async def check_payment_success(window_minutes: int = 30, platform: str = "all") -> dict:
    """Payment success rate now versus the same window 24 hours ago.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        platform: "all", "web", "ios" or "android".

    Returns:
        Current and reference success rates with attempt counts, and the change
        between them.
    """

    async def body() -> dict:
        w = get_env().world

        async def rate(ending_minutes_ago: int):
            mins = window_minutes if ending_minutes_ago == 0 else max(window_minutes, REFERENCE_MINUTES)
            start, end = window(w, mins, ending_minutes_ago)
            conds = [Payment.ts >= start, Payment.ts < end]
            if platform != "all":
                conds.append(Payment.platform == platform)
            async with w.sf() as s:
                total, ok = (
                    await s.execute(
                        select(
                            func.count(Payment.id),
                            func.sum(case((Payment.status == "success", 1), else_=0)),
                        ).where(and_(*conds))
                    )
                ).one()
            return (total or 0), (ok or 0)

        cur_n, cur_ok = await rate(0)
        ref_n, ref_ok = await rate(24 * 60)
        cur_rate, ref_rate = pct(cur_ok, cur_n), pct(ref_ok, ref_n)

        return {
            "metric": "payment_success_rate_pct",
            "platform": platform,
            "window_minutes": window_minutes,
            "current": {"rate_pct": cur_rate, "attempts": cur_n, "succeeded": cur_ok},
            "reference_24h_ago": {"rate_pct": ref_rate, "attempts": ref_n, "succeeded": ref_ok},
            "change": _delta(cur_rate, ref_rate),
            "sufficient_sample": cur_n >= 60 and ref_n >= 60,
        }

    return await invoke(
        "check_payment_success",
        {"window_minutes": window_minutes, "platform": platform},
        body,
    )


@tool(kind="verify", permission="READ")
async def check_error_rate(window_minutes: int = 30, error_code: str = "") -> dict:
    """How often a specific payment error is occurring, now versus 24 hours ago.

    Use this to confirm that a specific failure signature has actually stopped,
    rather than inferring it from an aggregate rate.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        error_code: The error code to count, for example "PAY_CFG_3021". Empty
            counts all failed payments.

    Returns:
        Current and reference counts for that error, and the change.
    """

    async def body() -> dict:
        w = get_env().world

        async def count(ending_minutes_ago: int) -> int:
            start, end = window(w, window_minutes, ending_minutes_ago)
            conds = [Payment.ts >= start, Payment.ts < end, Payment.status == "failed"]
            if error_code:
                conds.append(Payment.error_code == error_code)
            async with w.sf() as s:
                return (
                    await s.execute(select(func.count(Payment.id)).where(and_(*conds)))
                ).scalar_one()

        cur, ref = await count(0), await count(24 * 60)
        return {
            "metric": "failed_payments",
            "error_code": error_code or "any",
            "window_minutes": window_minutes,
            "current_count": cur,
            "reference_24h_ago_count": ref,
            "change": {"absolute": cur - ref},
        }

    return await invoke(
        "check_error_rate",
        {"window_minutes": window_minutes, "error_code": error_code},
        body,
    )


@tool(kind="verify", permission="READ")
async def wait_for_traffic(minutes: int) -> dict:
    """Let new traffic accumulate before verifying a change.

    A remediation only affects sessions served after it was applied. Checking a
    metric immediately after acting measures mostly old traffic and will look
    like no change. Wait long enough for a decent sample, then verify.

    Thirty minutes is usually enough on a busy platform; use more if the
    verification tools report `sufficient_sample: false`.

    Args:
        minutes: How many minutes of fresh traffic to wait for.

    Returns:
        The time waited and the number of sessions that arrived during it.
    """

    async def body() -> dict:
        env = get_env()
        w = env.world
        minutes_ = max(1, min(int(minutes), 240))
        before = w.now()

        if env.fast_forward:
            w.advance(minutes_)
        else:
            # Honest wait: the sim clock runs faster than wall time, so this
            # costs minutes_/speed real seconds.
            await asyncio.sleep(minutes_ * 60 / w.scenario.speed)
        await w.tick()

        async with w.sf() as s:
            arrived = (
                await s.execute(
                    select(func.count(Session.id)).where(
                        Session.ts >= before, Session.ts < w.now()
                    )
                )
            ).scalar_one()
        return {
            "waited_minutes": minutes_,
            "sessions_since": arrived,
            "now": w.now().isoformat(timespec="seconds"),
        }

    return await invoke("wait_for_traffic", {"minutes": minutes}, body)
