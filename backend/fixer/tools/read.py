"""Investigation tools.

These are read-only. Their docstrings are the schema the model plans against,
so they state units, allowed values and what each number means -- vague tool
descriptions are the most common reason an agent investigates badly.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import Integer, and_, case, desc, func, select

from ..sim.schema import (
    ConfigEntry,
    Deployment,
    FeatureFlag,
    LogEntry,
    Order,
    Payment,
    ServiceHealth,
    Session,
    SupportTicket,
)
from .base import get_env, invoke, pct, tool, window

_SPLITS = {
    "platform": Session.platform,
    "region": Session.region,
    "traffic_source": Session.traffic_source,
    "app_version": Session.app_version,
}


def _i(col) -> Any:
    """Sum a boolean column portably (SQLite has no native boolean)."""
    return func.sum(func.cast(col, Integer))


@tool(kind="read", permission="READ")
async def query_conversion_funnel(
    window_minutes: int = 60,
    ending_minutes_ago: int = 0,
    split_by: str = "none",
) -> dict:
    """Session counts through the purchase funnel, and the resulting conversion rate.

    The funnel is view -> add to cart -> start checkout -> convert. Conversion
    rate is converted sessions divided by all sessions, as a percentage.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".
            Use this to look at an earlier period, for example
            ending_minutes_ago=1440 for the same window yesterday.
        split_by: One of "none", "platform", "region", "traffic_source",
            "app_version". Splitting by platform separates web, ios and android.

    Returns:
        Overall totals, and one entry per segment when split_by is not "none".
        Each entry carries the session count it was computed from, so thin
        segments can be recognised as unreliable.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        cols = [
            func.count(Session.id),
            _i(Session.added_to_cart),
            _i(Session.checkout_started),
            _i(Session.converted),
        ]
        where = and_(Session.ts >= start, Session.ts < end)

        async with w.sf() as s:
            total, cart, checkout, conv = (
                await s.execute(select(*cols).where(where))
            ).one()
            out: dict = {
                "window": {
                    "minutes": window_minutes,
                    "ending_minutes_ago": ending_minutes_ago,
                    "from": start.isoformat(timespec="seconds"),
                    "to": end.isoformat(timespec="seconds"),
                },
                "overall": {
                    "sessions": total or 0,
                    "added_to_cart": cart or 0,
                    "checkout_started": checkout or 0,
                    "converted": conv or 0,
                    "conversion_rate_pct": pct(conv, total),
                    "checkout_completion_pct": pct(conv, checkout),
                },
            }
            if split_by in _SPLITS:
                col = _SPLITS[split_by]
                rows = (
                    await s.execute(
                        select(col, *cols).where(where).group_by(col).order_by(desc(func.count(Session.id)))
                    )
                ).all()
                out["split_by"] = split_by
                out["segments"] = [
                    {
                        split_by: seg,
                        "sessions": t or 0,
                        "checkout_started": ck or 0,
                        "converted": cv or 0,
                        "conversion_rate_pct": pct(cv, t),
                        "checkout_completion_pct": pct(cv, ck),
                    }
                    for seg, t, _c, ck, cv in rows
                ]
            elif split_by != "none":
                out["warning"] = f"unknown split_by {split_by!r}; allowed: none, {', '.join(_SPLITS)}"
        return out

    return await invoke(
        "query_conversion_funnel",
        {"window_minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago, "split_by": split_by},
        body,
    )


@tool(kind="read", permission="READ")
async def query_payments(
    window_minutes: int = 60,
    ending_minutes_ago: int = 0,
    split_by: str = "none",
) -> dict:
    """Payment attempt outcomes, broken down by failure reason.

    Every checkout that reaches the payment step produces one payment attempt,
    which either succeeds or fails with an error code.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".
        split_by: "none" or "platform".

    Returns:
        Success and failure counts, the failure rate as a percentage, and a
        breakdown of failures by error code. Error codes beginning CARD_,
        INSUFFICIENT_ or EXPIRED_ are ordinary customer-side declines and occur
        at all times; other codes are worth investigating.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        where = and_(Payment.ts >= start, Payment.ts < end)

        async with w.sf() as s:
            total, ok = (
                await s.execute(
                    select(
                        func.count(Payment.id),
                        func.sum(case((Payment.status == "success", 1), else_=0)),
                    ).where(where)
                )
            ).one()
            codes = (
                await s.execute(
                    select(Payment.error_code, func.count(Payment.id))
                    .where(where, Payment.status == "failed")
                    .group_by(Payment.error_code)
                    .order_by(desc(func.count(Payment.id)))
                )
            ).all()
            out: dict = {
                "window": {"minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
                "attempts": total or 0,
                "succeeded": ok or 0,
                "failed": (total or 0) - (ok or 0),
                "failure_rate_pct": pct((total or 0) - (ok or 0), total),
                "failures_by_error_code": [
                    {"error_code": c or "UNKNOWN", "count": n} for c, n in codes
                ],
            }
            if split_by == "platform":
                rows = (
                    await s.execute(
                        select(
                            Payment.platform,
                            func.count(Payment.id),
                            func.sum(case((Payment.status == "success", 1), else_=0)),
                        )
                        .where(where)
                        .group_by(Payment.platform)
                    )
                ).all()
                out["by_platform"] = [
                    {
                        "platform": p,
                        "attempts": t,
                        "succeeded": s_ or 0,
                        "failed": t - (s_ or 0),
                        "failure_rate_pct": pct(t - (s_ or 0), t),
                    }
                    for p, t, s_ in rows
                ]
        return out

    return await invoke(
        "query_payments",
        {"window_minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago, "split_by": split_by},
        body,
    )


@tool(kind="read", permission="READ")
async def query_orders(window_minutes: int = 60, ending_minutes_ago: int = 0) -> dict:
    """Order counts and revenue, grouped by order status and platform.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".

    Returns:
        Counts and total value per status (paid, failed, pending), plus a
        per-platform breakdown. Amounts are in cents.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        where = and_(Order.ts >= start, Order.ts < end)
        async with w.sf() as s:
            by_status = (
                await s.execute(
                    select(Order.status, func.count(Order.id), func.sum(Order.amount_cents))
                    .where(where)
                    .group_by(Order.status)
                )
            ).all()
            by_plat = (
                await s.execute(
                    select(
                        Order.platform,
                        func.count(Order.id),
                        func.sum(case((Order.status == "paid", 1), else_=0)),
                    )
                    .where(where)
                    .group_by(Order.platform)
                )
            ).all()
        return {
            "window": {"minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
            "by_status": [
                {"status": st, "orders": n, "value_cents": int(v or 0)} for st, n, v in by_status
            ],
            "by_platform": [
                {"platform": p, "orders": n, "paid": int(paid or 0)} for p, n, paid in by_plat
            ],
        }

    return await invoke(
        "query_orders",
        {"window_minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
        body,
    )


@tool(kind="read", permission="READ")
async def query_logs(
    window_minutes: int = 60,
    ending_minutes_ago: int = 0,
    level: str = "ERROR",
    service: str = "",
    platform: str = "",
    limit: int = 8,
) -> dict:
    """Application logs, aggregated by error code with a sample of messages.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".
        level: "ERROR", "WARN", "INFO", or "" for all levels.
        service: Restrict to one service, or "" for all. Services are
            checkout-svc, payments-svc, search-svc, catalog-svc, web-edge.
        platform: Restrict to "web", "ios", "android", or "" for all.
        limit: Maximum number of sample messages to return.

    Returns:
        Counts grouped by error code and service, plus up to `limit` sample log
        lines. Log text often names the specific component or setting involved.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        conds = [LogEntry.ts >= start, LogEntry.ts < end]
        if level:
            conds.append(LogEntry.level == level)
        if service:
            conds.append(LogEntry.service == service)
        if platform:
            conds.append(LogEntry.platform == platform)
        where = and_(*conds)
        async with w.sf() as s:
            groups = (
                await s.execute(
                    select(LogEntry.service, LogEntry.error_code, func.count(LogEntry.id))
                    .where(where)
                    .group_by(LogEntry.service, LogEntry.error_code)
                    .order_by(desc(func.count(LogEntry.id)))
                    .limit(12)
                )
            ).all()
            samples = (
                await s.execute(
                    select(LogEntry.ts, LogEntry.service, LogEntry.level, LogEntry.platform, LogEntry.message)
                    .where(where)
                    .order_by(desc(LogEntry.ts))
                    .limit(max(1, min(limit, 25)))
                )
            ).all()
        return {
            "window": {"minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
            "counts": [
                {"service": sv, "error_code": ec, "count": n} for sv, ec, n in groups
            ],
            "samples": [
                {
                    "ts": ts.isoformat(timespec="seconds"),
                    "service": sv,
                    "level": lv,
                    "platform": pl,
                    "message": msg,
                }
                for ts, sv, lv, pl, msg in samples
            ],
        }

    return await invoke(
        "query_logs",
        {
            "window_minutes": window_minutes,
            "ending_minutes_ago": ending_minutes_ago,
            "level": level,
            "service": service,
            "platform": platform,
            "limit": limit,
        },
        body,
    )


@tool(kind="read", permission="READ")
async def query_deployments(hours_back: int = 24, service: str = "") -> dict:
    """Recent code deployments, most recent first.

    Args:
        hours_back: How far back to look, in hours.
        service: Restrict to one service, or "" for all.

    Returns:
        Deployment reference, service, version, author, summary, timestamp and
        current status (live or rolled_back). A deployment shortly before a
        problem began is a common suspect, but correlation in time is not
        proof of cause.
    """

    async def body() -> dict:
        w = get_env().world
        cutoff = w.now() - timedelta(hours=max(1, hours_back))
        conds = [Deployment.ts >= cutoff]
        if service:
            conds.append(Deployment.service == service)
        async with w.sf() as s:
            rows = (
                await s.execute(
                    select(Deployment).where(and_(*conds)).order_by(desc(Deployment.ts))
                )
            ).scalars().all()
        return {
            "hours_back": hours_back,
            "deployments": [
                {
                    "ref": d.ref,
                    "service": d.service,
                    "version": d.version,
                    "author": d.author,
                    "summary": d.summary,
                    "deployed_at": d.ts.isoformat(timespec="seconds"),
                    "minutes_ago": int((w.now() - d.ts).total_seconds() // 60),
                    "status": d.status,
                }
                for d in rows
            ],
        }

    return await invoke(
        "query_deployments", {"hours_back": hours_back, "service": service}, body
    )


@tool(kind="read", permission="READ")
async def query_configuration(key_prefix: str = "", changed_within_hours: int = 0) -> dict:
    """Runtime configuration values and their recent change history.

    Runtime configuration is versioned separately from code. Rolling back a
    deployment does not revert configuration changes that deployment made.

    Args:
        key_prefix: Return only keys starting with this string, for example
            "payments." or "risk.". Empty returns everything.
        changed_within_hours: If greater than 0, return only keys changed
            within this many hours. Configuration changes constantly during
            normal operation, so a recent change is not by itself suspicious.

    Returns:
        Each key with its current value, the value it held before the last
        change, when it changed, and who or what changed it. An `updated_by`
        naming a release script ties a configuration change to a deployment.
    """

    async def body() -> dict:
        w = get_env().world
        conds = []
        if key_prefix:
            conds.append(ConfigEntry.key.like(f"{key_prefix}%"))
        if changed_within_hours > 0:
            conds.append(ConfigEntry.updated_ts >= w.now() - timedelta(hours=changed_within_hours))
        q = select(ConfigEntry).order_by(desc(ConfigEntry.updated_ts))
        if conds:
            q = q.where(and_(*conds))
        async with w.sf() as s:
            rows = (await s.execute(q)).scalars().all()
        return {
            "matched": len(rows),
            "entries": [
                {
                    "key": c.key,
                    "value": c.value,
                    "previous_value": c.previous_value,
                    "changed": c.previous_value is not None and c.previous_value != c.value,
                    "updated_at": c.updated_ts.isoformat(timespec="seconds"),
                    "minutes_ago": int((w.now() - c.updated_ts).total_seconds() // 60),
                    "updated_by": c.updated_by,
                    "description": c.description,
                }
                for c in rows
            ],
        }

    return await invoke(
        "query_configuration",
        {"key_prefix": key_prefix, "changed_within_hours": changed_within_hours},
        body,
    )


@tool(kind="read", permission="READ")
async def query_feature_flags(changed_within_hours: int = 0) -> dict:
    """Feature flags, their on/off state and rollout percentage.

    Args:
        changed_within_hours: If greater than 0, return only flags changed
            within this many hours.

    Returns:
        Each flag with enabled state, rollout percentage, when it last changed
        and who changed it.
    """

    async def body() -> dict:
        w = get_env().world
        q = select(FeatureFlag).order_by(desc(FeatureFlag.updated_ts))
        if changed_within_hours > 0:
            q = q.where(FeatureFlag.updated_ts >= w.now() - timedelta(hours=changed_within_hours))
        async with w.sf() as s:
            rows = (await s.execute(q)).scalars().all()
        return {
            "flags": [
                {
                    "key": f.key,
                    "enabled": f.enabled,
                    "rollout_pct": f.rollout_pct,
                    "updated_at": f.updated_ts.isoformat(timespec="seconds"),
                    "minutes_ago": int((w.now() - f.updated_ts).total_seconds() // 60),
                    "updated_by": f.updated_by,
                    "description": f.description,
                }
                for f in rows
            ]
        }

    return await invoke(
        "query_feature_flags", {"changed_within_hours": changed_within_hours}, body
    )


@tool(kind="read", permission="READ")
async def query_support_tickets(
    window_minutes: int = 180, ending_minutes_ago: int = 0, limit: int = 10
) -> dict:
    """Customer support tickets raised in a window.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".
        limit: Maximum number of tickets to return.

    Returns:
        Ticket subjects and bodies with the platform the customer was using.
        Customers describe symptoms in their own words, which can point at a
        component that metrics alone do not isolate.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        async with w.sf() as s:
            rows = (
                await s.execute(
                    select(SupportTicket)
                    .where(SupportTicket.ts >= start, SupportTicket.ts < end)
                    .order_by(desc(SupportTicket.ts))
                    .limit(max(1, min(limit, 30)))
                )
            ).scalars().all()
            by_platform = (
                await s.execute(
                    select(SupportTicket.platform, func.count(SupportTicket.id))
                    .where(SupportTicket.ts >= start, SupportTicket.ts < end)
                    .group_by(SupportTicket.platform)
                )
            ).all()
        return {
            "window": {"minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
            "counts_by_platform": [
                {"platform": p or "unspecified", "count": n} for p, n in by_platform
            ],
            "tickets": [
                {
                    "ts": t.ts.isoformat(timespec="seconds"),
                    "platform": t.platform,
                    "subject": t.subject,
                    "body": t.body,
                    "tags": t.tags,
                }
                for t in rows
            ],
        }

    return await invoke(
        "query_support_tickets",
        {"window_minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago, "limit": limit},
        body,
    )


@tool(kind="read", permission="READ")
async def query_infrastructure(
    window_minutes: int = 60, ending_minutes_ago: int = 0, service: str = ""
) -> dict:
    """Service health: error rate, tail latency and CPU.

    Args:
        window_minutes: Length of the window to measure, in minutes.
        ending_minutes_ago: How long ago the window ends. 0 means "up to now".
        service: Restrict to one service, or "" for all.

    Returns:
        Per service: mean error rate as a fraction, mean and peak p95 latency
        in milliseconds, mean CPU percentage, and how many samples were
        unhealthy.
    """

    async def body() -> dict:
        w = get_env().world
        start, end = window(w, window_minutes, ending_minutes_ago)
        conds = [ServiceHealth.ts >= start, ServiceHealth.ts < end]
        if service:
            conds.append(ServiceHealth.service == service)
        async with w.sf() as s:
            rows = (
                await s.execute(
                    select(
                        ServiceHealth.service,
                        func.avg(ServiceHealth.error_rate),
                        func.avg(ServiceHealth.latency_p95_ms),
                        func.max(ServiceHealth.latency_p95_ms),
                        func.avg(ServiceHealth.cpu_pct),
                        func.sum(case((ServiceHealth.healthy == False, 1), else_=0)),  # noqa: E712
                        func.count(ServiceHealth.id),
                    )
                    .where(and_(*conds))
                    .group_by(ServiceHealth.service)
                    .order_by(desc(func.avg(ServiceHealth.error_rate)))
                )
            ).all()
        return {
            "window": {"minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago},
            "services": [
                {
                    "service": sv,
                    "error_rate_mean": round(er or 0, 5),
                    "latency_p95_mean_ms": int(lm or 0),
                    "latency_p95_max_ms": int(lx or 0),
                    "cpu_pct_mean": round(cpu or 0, 1),
                    "unhealthy_samples": int(unh or 0),
                    "samples": n,
                }
                for sv, er, lm, lx, cpu, unh, n in rows
            ],
        }

    return await invoke(
        "query_infrastructure",
        {"window_minutes": window_minutes, "ending_minutes_ago": ending_minutes_ago, "service": service},
        body,
    )
