"""The NovaCart world.

Responsibilities:

* hold a simulated clock that runs faster than wall time, so a mission that
  would take a human an afternoon fits inside a four-minute demo;
* generate real rows -- history up front, then incrementally as sim time
  advances -- with any active incident's effects folded into the generation;
* apply agent actions, and recompute which causes are still active.

The important property: **metric recovery is computed, never scripted.** If the
agent removes the real cause, later rows are generated without its effects and
the verification query sees genuine recovery. If it removes something else, the
numbers do not move.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from . import incidents as inc
from .schema import (
    Base,
    ConfigEntry,
    Deployment,
    FeatureFlag,
    LogEntry,
    Order,
    Payment,
    ServiceHealth,
    Session,
    SupportTicket,
    WorldState,
)

# ---------------------------------------------------------------------------
# Traffic model
# ---------------------------------------------------------------------------

PLATFORMS = [("web", 0.44), ("ios", 0.38), ("android", 0.18)]
REGIONS = [("US", 0.42), ("DE", 0.18), ("UK", 0.15), ("FR", 0.11), ("JP", 0.08), ("BR", 0.06)]
SOURCES = [("organic", 0.34), ("paid_search", 0.26), ("email", 0.18), ("social", 0.14), ("direct", 0.08)]
SERVICES = ["checkout-svc", "payments-svc", "search-svc", "catalog-svc", "web-edge"]

# Baseline funnel rates, tuned so overall conversion sits near 3.7%.
RATE_CART = 0.22
RATE_CHECKOUT = 0.40
RATE_CONVERT = 0.42

# Baseline payment failure rate on otherwise-healthy traffic.
BASELINE_PAYMENT_FAILURE = 0.038
BASELINE_ERROR_CODES = [
    ("CARD_DECLINED", 0.55),
    ("INSUFFICIENT_FUNDS", 0.22),
    ("EXPIRED_CARD", 0.13),
    ("RISK_HOLD", 0.10),
]

APP_VERSIONS = {"web": ["w-3.4.1"], "ios": ["i-6.2.0", "i-6.1.4"], "android": ["a-5.9.2"]}


def _pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    r = rng.random()
    acc = 0.0
    for name, w in weighted:
        acc += w
        if r <= acc:
            return name
    return weighted[-1][0]


def _diurnal(ts: datetime) -> float:
    """Traffic multiplier by hour of day: trough around 04:00, peak around 16:00.

    Stays within roughly [0.5, 1.5]. It must never approach zero -- a starved
    window makes conversion rate statistically meaningless, and the verifier
    reads conversion rate over short windows.
    """
    h = ts.hour + ts.minute / 60.0
    return (
        1.0
        + 0.38 * math.sin((h - 10.0) / 24.0 * 2 * math.pi)
        + 0.10 * math.sin((h - 8.0) / 12.0 * 2 * math.pi)
    )


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    scenario_id: str
    incident_key: str
    seed: int
    sim_start: datetime  # sim time at which the mission begins
    real_start: datetime  # wall clock at which the mission began
    incident_start: datetime  # sim time the cause was introduced
    speed: float = 60.0  # sim seconds per real second
    history_days: float = 1.0
    sessions_per_min: int = 250
    generated_to: datetime | None = None  # sim time of last generated row
    resolved_at: datetime | None = None  # sim time the cause was removed
    applied_actions: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        d = dict(self.__dict__)
        for k in ("sim_start", "real_start", "incident_start", "generated_to", "resolved_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "Scenario":
        d = json.loads(raw)
        for k in ("sim_start", "real_start", "incident_start", "generated_to", "resolved_at"):
            d[k] = datetime.fromisoformat(d[k]) if d[k] else None
        return cls(**d)


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


class World(inc.WorldWriter):
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.sf: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        self.scenario: Scenario | None = None
        self._pending: list[tuple[type, dict]] = []

    # -- lifecycle ----------------------------------------------------------

    async def reset_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    async def start_scenario(
        self,
        incident_key: str = "payment_config_regression",
        seed: int | None = None,
        speed: float = 60.0,
        history_days: float = 1.0,
        sessions_per_min: int = 250,
        minutes_since_incident: int = 90,
    ) -> Scenario:
        seed = seed if seed is not None else random.randrange(1_000_000)
        now_sim = datetime(2026, 8, 19, 14, 30, 0)
        sc = Scenario(
            scenario_id=f"nc-{seed}",
            incident_key=incident_key,
            seed=seed,
            sim_start=now_sim,
            real_start=datetime.utcnow(),
            incident_start=now_sim - timedelta(minutes=minutes_since_incident),
            speed=speed,
            history_days=history_days,
            sessions_per_min=sessions_per_min,
        )
        self.scenario = sc

        await self.reset_schema()
        await self._seed_static_world()

        incident = inc.get(incident_key)
        incident.setup(self, sc.incident_start)
        for d in incident.distractors:
            d(self, sc.incident_start)
        await self._flush()

        await self._generate(sc.sim_start - timedelta(days=history_days), sc.sim_start)
        sc.generated_to = sc.sim_start
        await self._save_scenario()
        return sc

    def now(self) -> datetime:
        """Current sim time."""
        assert self.scenario is not None
        elapsed_real = (datetime.utcnow() - self.scenario.real_start).total_seconds()
        return self.scenario.sim_start + timedelta(
            seconds=elapsed_real * self.scenario.speed
        )

    def advance(self, minutes: float) -> None:
        """Jump the sim clock forward without waiting.

        Used by tests and by the evaluation harness, which runs hundreds of
        missions and cannot spend real time waiting for metrics to move.
        """
        assert self.scenario is not None
        self.scenario.real_start -= timedelta(
            seconds=minutes * 60 / self.scenario.speed
        )

    async def tick(self) -> None:
        """Bring generated data up to the current sim time. Cheap and idempotent."""
        sc = self.scenario
        if sc is None or sc.generated_to is None:
            return
        target = self.now()
        if target <= sc.generated_to:
            return
        await self._generate(sc.generated_to, target)
        sc.generated_to = target
        await self._save_scenario()

    # -- agent actions ------------------------------------------------------

    async def apply_action(self, action: inc.Action) -> dict:
        """Mutate the world. Returns the tool-level result only.

        Note what this deliberately does *not* return: whether the mission is
        now solved. A tool reporting success is not evidence the objective was
        met -- that is the verifier's job, against real metrics.
        """
        await self.tick()
        sc = self.scenario
        assert sc is not None
        ts = self.now()
        result: dict = {"applied": False, "detail": ""}

        async with self.sf() as s:
            if action.kind == "rollback_deployment":
                dep = (
                    await s.execute(select(Deployment).where(Deployment.ref == action.target))
                ).scalar_one_or_none()
                if dep is None:
                    result = {"applied": False, "detail": f"no deployment {action.target}"}
                else:
                    dep.status = "rolled_back"
                    result = {
                        "applied": True,
                        "detail": f"deployment {dep.ref} ({dep.service} {dep.version}) rolled back",
                        "note": "Runtime configuration is versioned separately and is not "
                        "reverted by a deployment rollback.",
                    }

            elif action.kind in ("update_configuration", "restore_configuration"):
                row = (
                    await s.execute(select(ConfigEntry).where(ConfigEntry.key == action.target))
                ).scalar_one_or_none()
                if row is None:
                    result = {"applied": False, "detail": f"no config key {action.target}"}
                else:
                    if action.kind == "restore_configuration":
                        new_value = row.previous_value or row.value
                    else:
                        new_value = str(action.params.get("value", row.value))
                    row.previous_value, row.value = row.value, new_value
                    row.updated_ts, row.updated_by = ts, "the-fixer"
                    result = {
                        "applied": True,
                        "detail": f"{row.key}: {row.previous_value} -> {row.value}",
                    }

            elif action.kind == "disable_feature":
                row = (
                    await s.execute(select(FeatureFlag).where(FeatureFlag.key == action.target))
                ).scalar_one_or_none()
                if row is None:
                    result = {"applied": False, "detail": f"no flag {action.target}"}
                else:
                    row.enabled = False
                    row.updated_ts, row.updated_by = ts, "the-fixer"
                    result = {"applied": True, "detail": f"flag {row.key} disabled"}

            elif action.kind == "restart_service":
                result = {
                    "applied": True,
                    "detail": f"{action.target} restarted",
                    "note": "Process restarted; configuration reloaded from current values.",
                }

            else:
                result = {"applied": False, "detail": f"unknown action {action.kind}"}

            await s.commit()

        sc.applied_actions.append(
            {"kind": action.kind, "target": action.target, "params": action.params,
             "ts": ts.isoformat(), "applied": result.get("applied", False)}
        )

        # Did that remove the cause? Nothing announces this -- it only changes
        # how rows are generated from here on.
        if result.get("applied") and sc.resolved_at is None:
            if inc.get(sc.incident_key).is_fixed_by(action):
                sc.resolved_at = ts

        await self._save_scenario()
        return result

    # -- WorldWriter (used by incident setup) -------------------------------

    def set_config(self, key, value, ts, by, description="") -> None:
        self._pending.append(
            (ConfigEntry, {"key": key, "value": value, "previous_value": None,
                           "updated_ts": ts, "updated_by": by, "description": description})
        )

    def add_deployment(self, ref, service, version, author, summary, ts) -> None:
        self._pending.append(
            (Deployment, {"ts": ts, "ref": ref, "service": service, "version": version,
                          "author": author, "summary": summary, "status": "live"})
        )

    def set_flag(self, key, enabled, ts, by, rollout_pct=100, description="") -> None:
        self._pending.append(
            (FeatureFlag, {"key": key, "enabled": enabled, "rollout_pct": rollout_pct,
                           "updated_ts": ts, "updated_by": by, "description": description})
        )

    # -- internals ----------------------------------------------------------

    async def _flush(self) -> None:
        """Write pending rows.

        ConfigEntry and FeatureFlag are keyed by name and are written more than
        once -- the static seed establishes them, then an incident overwrites
        one. Those upsert, and an overwrite records ``previous_value`` so that
        ``restore_configuration`` has something real to restore to.
        """
        if not self._pending:
            return
        keyed = {ConfigEntry: "key", FeatureFlag: "key"}
        plain: dict[type, list[dict]] = {}

        async with self.sf() as s:
            for model, row in self._pending:
                pk = keyed.get(model)
                if pk is None:
                    plain.setdefault(model, []).append(row)
                    continue
                existing = (
                    await s.execute(
                        select(model).where(getattr(model, pk) == row[pk])
                    )
                ).scalar_one_or_none()
                if existing is None:
                    await s.execute(insert(model), [row])
                    continue
                if model is ConfigEntry and row["value"] != existing.value:
                    row["previous_value"] = existing.value
                for col, val in row.items():
                    setattr(existing, col, val)
            for model, rows in plain.items():
                await s.execute(insert(model), rows)
            await s.commit()
        self._pending.clear()

    async def _save_scenario(self) -> None:
        assert self.scenario is not None
        async with self.sf() as s:
            await s.execute(delete(WorldState).where(WorldState.key == "scenario"))
            await s.execute(
                insert(WorldState), [{"key": "scenario", "value": self.scenario.to_json()}]
            )
            await s.commit()

    async def _seed_static_world(self) -> None:
        """Config, flags and deployments that exist regardless of any incident."""
        sc = self.scenario
        assert sc is not None
        base = sc.sim_start - timedelta(days=sc.history_days)

        for key, value, desc in [
            ("payments.ios.provider_profile", "standard_v4", "Provider profile used when tokenising iOS wallet payments."),
            ("payments.web.provider_profile", "standard_v4", "Provider profile for web card payments."),
            ("payments.retry.max_attempts", "3", "Payment retry attempts before hard failure."),
            ("checkout.session_ttl_minutes", "30", "Checkout session lifetime."),
            ("risk.fraud_score_threshold", "82", "Block orders scoring above this."),
            ("catalog.cache_ttl_seconds", "300", "Product catalogue cache TTL."),
            ("marketing.campaign.de_summer.active", "true", "Regional campaign toggle."),
        ]:
            self.set_config(key, value, base, "platform-team", desc)

        for key, enabled, desc in [
            ("checkout.express_wallet", True, "One-tap wallet checkout."),
            ("checkout.address_autocomplete", True, "Address autocomplete in checkout."),
            ("search.semantic_ranking", True, "Semantic ranking in search results."),
            ("risk.strict_velocity_checks", False, "Aggressive velocity fraud checks."),
        ]:
            self.set_flag(key, enabled, base, "platform-team", 100, desc)

        rng = random.Random(sc.seed ^ 0xD3)
        for i in range(24):
            ts = base + timedelta(hours=rng.uniform(0, sc.history_days * 24 - 3))
            svc = rng.choice(SERVICES)
            self.add_deployment(
                ref=str(8440 + i),
                service=svc,
                version=f"v{rng.randint(1, 6)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
                author=rng.choice(["m.okafor", "l.haddad", "c.rivera", "s.novak", "t.bergman"]),
                summary=rng.choice(
                    [
                        f"{svc}: dependency bump",
                        f"{svc}: add structured logging",
                        f"{svc}: cache warming on boot",
                        f"{svc}: tighten request timeouts",
                        f"{svc}: metrics instrumentation",
                    ]
                ),
                ts=ts,
            )
        await self._flush()

    def _active_effects(self, ts: datetime) -> list[inc.Effect]:
        """Effects in force at sim time ``ts``."""
        sc = self.scenario
        assert sc is not None
        if ts < sc.incident_start:
            return []
        if sc.resolved_at is not None and ts >= sc.resolved_at:
            return []
        return inc.get(sc.incident_key).effects

    async def _generate(self, start: datetime, end: datetime) -> None:
        """Generate every row for the half-open sim interval [start, end)."""
        sc = self.scenario
        assert sc is not None
        rng = random.Random(sc.seed ^ int(start.timestamp()))
        incident = inc.get(sc.incident_key)

        sessions: list[dict] = []
        orders: list[dict] = []
        payments: list[dict] = []
        health: list[dict] = []
        logs: list[dict] = []
        tickets: list[dict] = []

        # Ids must be unique across incremental generations.
        base_id = int((start - datetime(2026, 1, 1)).total_seconds()) * 1000

        minutes = int((end - start).total_seconds() // 60)
        for m in range(minutes):
            ts_min = start + timedelta(minutes=m)
            effects = self._active_effects(ts_min)
            n = max(1, int(sc.sessions_per_min * _diurnal(ts_min) * rng.uniform(0.85, 1.15)))

            for k in range(n):
                sid = base_id + m * 1000 + k
                ts = ts_min + timedelta(seconds=rng.randint(0, 59))
                platform = _pick(rng, PLATFORMS)
                region = _pick(rng, REGIONS)
                source = _pick(rng, SOURCES)

                # The DE campaign ending really does reduce DE traffic -- a real
                # change that is not the cause of anything.
                if region == "DE" and ts >= sc.incident_start - timedelta(hours=2):
                    if rng.random() < 0.45:
                        continue

                matching = [e for e in effects if e.matches(platform=platform, region=region)]

                added = rng.random() < RATE_CART
                checkout = added and rng.random() < RATE_CHECKOUT
                converted = checkout and rng.random() < RATE_CONVERT
                abandon_stage = None
                incident_hit = False

                if checkout and converted:
                    boost = sum(e.checkout_failure_boost for e in matching)
                    if boost and rng.random() < boost:
                        converted = False
                        incident_hit = True
                        abandon_stage = next(
                            (e.abandon_stage for e in matching if e.checkout_failure_boost), "payment"
                        )
                elif checkout and not converted:
                    abandon_stage = rng.choice(["payment", "shipping", "review"])

                sessions.append(
                    {"id": sid, "ts": ts, "platform": platform, "region": region,
                     "traffic_source": source, "app_version": rng.choice(APP_VERSIONS[platform]),
                     "viewed_product": True, "added_to_cart": added,
                     "checkout_started": checkout, "converted": converted,
                     "abandon_stage": abandon_stage}
                )

                if not checkout:
                    continue

                # Every checkout attempt produces an order and a payment attempt.
                amount = rng.randint(1800, 24000)
                if converted:
                    o_status, p_status, err = "paid", "success", None
                elif incident_hit:
                    code = next(
                        (e.payment_error_code for e in matching if e.payment_error_code), None
                    )
                    if code:
                        o_status, p_status, err = "failed", "failed", code
                    else:
                        o_status, p_status, err = "pending", "failed", None
                elif abandon_stage == "payment" and rng.random() < BASELINE_PAYMENT_FAILURE / RATE_CONVERT:
                    o_status, p_status, err = "failed", "failed", _pick(rng, BASELINE_ERROR_CODES)
                else:
                    continue  # abandoned before a payment attempt

                orders.append(
                    {"id": sid, "ts": ts, "session_id": sid, "platform": platform,
                     "region": region, "amount_cents": amount, "status": o_status}
                )
                profile = "standard_v4"
                if platform == "ios" and incident_hit:
                    profile = "legacy_v2"
                payments.append(
                    {"id": sid, "ts": ts, "order_id": sid, "platform": platform,
                     "provider": "stripe" if platform != "ios" else "adyen",
                     "profile": profile, "status": p_status, "error_code": err,
                     "latency_ms": rng.randint(180, 900) + (600 if err else 0)}
                )

            # Infrastructure samples, one per service per minute.
            for svc in SERVICES:
                svc_eff = [e for e in effects if e.service == svc]
                err_rate = rng.uniform(0.001, 0.006)
                lat = rng.randint(70, 220)
                if svc == "payments-svc" and any(e.payment_error_code for e in effects):
                    err_rate += rng.uniform(0.05, 0.11)
                for e in svc_eff:
                    if e.service_error_rate is not None:
                        err_rate += e.service_error_rate
                    lat = int(lat * e.latency_multiplier)
                health.append(
                    {"ts": ts_min, "service": svc, "error_rate": round(err_rate, 5),
                     "latency_p95_ms": lat, "cpu_pct": round(rng.uniform(18, 62), 1),
                     "healthy": err_rate < 0.08}
                )

            # Logs: steady background noise plus any incident signature.
            for _ in range(rng.randint(1, 3)):
                logs.append(
                    {"ts": ts_min + timedelta(seconds=rng.randint(0, 59)),
                     "service": rng.choice(SERVICES), "level": "INFO", "platform": None,
                     "error_code": None, "message": rng.choice(
                         ["request completed", "cache refreshed", "healthcheck ok",
                          "scheduled job finished"])}
                )
            for e in effects:
                for _ in range(e.logs_per_minute):
                    logs.append(
                        {"ts": ts_min + timedelta(seconds=rng.randint(0, 59)),
                         "service": e.log_service or "checkout-svc", "level": "ERROR",
                         "platform": e.scope.get("platform"),
                         "error_code": e.payment_error_code,
                         "message": e.log_message or "unhandled error"}
                    )

            # Support tickets: a trickle normally, a spike once customers notice.
            if rng.random() < 0.02:
                tickets.append(
                    {"ts": ts_min, "platform": None,
                     "subject": rng.choice(["Where is my order?", "Return request",
                                            "Discount code not working", "Change delivery address"]),
                     "body": "Customer enquiry.", "tags": "general"}
                )
            if effects and incident.ticket_templates and rng.random() < 0.16:
                subj, body, plat = rng.choice(incident.ticket_templates)
                tickets.append(
                    {"ts": ts_min, "platform": plat, "subject": subj, "body": body,
                     "tags": "checkout,payment"}
                )

        async with self.sf() as s:
            for model, rows in (
                (Session, sessions), (Order, orders), (Payment, payments),
                (ServiceHealth, health), (LogEntry, logs), (SupportTicket, tickets),
            ):
                if rows:
                    for i in range(0, len(rows), 5000):
                        await s.execute(insert(model), rows[i : i + 5000])
            await s.commit()
