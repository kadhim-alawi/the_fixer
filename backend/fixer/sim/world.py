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

import asyncio
import json
import math
import os
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

# History must extend past 24h by at least the width of the baseline window the
# verification tools use, or a reference window ending "24h ago" falls off the
# start of generated data and reads as zero. 1.3 days leaves ~7h of margin.
#
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

# Runtime configuration. Most of these exist purely so that "which setting
# changed recently?" is a real question with a long answer.
CONFIG_KEYS: list[tuple[str, str, str]] = [
    ("payments.ios.provider_profile", "standard_v4", "Provider profile used when tokenising iOS wallet payments."),
    ("payments.web.provider_profile", "standard_v4", "Provider profile for web card payments."),
    ("payments.android.provider_profile", "standard_v4", "Provider profile for Android wallet payments."),
    ("payments.retry.max_attempts", "3", "Payment retry attempts before hard failure."),
    ("payments.capture_mode", "automatic", "Capture funds automatically or on fulfilment."),
    ("payments.provider.primary", "stripe", "Primary payment provider. Documented failover: adyen."),
    ("checkout.session_ttl_minutes", "30", "Checkout session lifetime."),
    ("checkout.address_validation", "strict", "How strictly delivery addresses are validated."),
    ("checkout.guest_enabled", "true", "Allow checkout without an account."),
    ("risk.fraud_score_threshold", "82", "Block orders scoring above this."),
    ("risk.velocity_window_minutes", "15", "Window for repeat-purchase velocity checks."),
    ("catalog.cache_ttl_seconds", "300", "Product catalogue cache TTL."),
    ("catalog.image_variant", "webp_v2", "Image format served to clients."),
    ("search.result_page_size", "24", "Results per search page."),
    ("search.ranking_model", "rank_v7", "Ranking model used by search."),
    ("web.edge_cache_seconds", "60", "Edge cache lifetime for storefront pages."),
    ("web.rate_limit_rpm", "600", "Per-client request ceiling."),
    ("marketing.campaign.de_summer.active", "true", "Regional campaign toggle."),
    ("notifications.order_email_template", "tpl_v12", "Order confirmation email template."),
]

CONFIG_CHURN_VALUES: dict[str, list[str]] = {
    "payments.retry.max_attempts": ["2", "4", "5"],
    "payments.capture_mode": ["manual", "automatic"],
    "payments.provider.primary": ["stripe"],
    "payments.web.provider_profile": ["standard_v4", "standard_v5"],
    "payments.android.provider_profile": ["standard_v4", "standard_v5"],
    "checkout.session_ttl_minutes": ["20", "45", "60"],
    "checkout.address_validation": ["lenient", "strict"],
    "checkout.guest_enabled": ["true", "false"],
    "risk.fraud_score_threshold": ["76", "80", "88"],
    "risk.velocity_window_minutes": ["10", "20", "30"],
    "catalog.cache_ttl_seconds": ["120", "600", "900"],
    "catalog.image_variant": ["webp_v2", "avif_v1"],
    "search.result_page_size": ["12", "36", "48"],
    "search.ranking_model": ["rank_v6", "rank_v8"],
    "web.edge_cache_seconds": ["30", "120", "300"],
    "web.rate_limit_rpm": ["400", "800", "1200"],
    "marketing.campaign.de_summer.active": ["true", "false"],
    "notifications.order_email_template": ["tpl_v11", "tpl_v13"],
}


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
    history_days: float = 1.3
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
        # When set, the sim clock ignores wall time. See freeze().
        self._frozen_now: datetime | None = None
        # Generation must not run twice concurrently. Mission Control polls
        # metrics while the agent is working, so tick() is genuinely reentrant
        # from two tasks; without this both generate the same minute range and
        # collide on primary keys.
        self._gen_lock = asyncio.Lock()

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
        history_days: float = 1.3,
        sessions_per_min: int = 250,
        minutes_since_incident: int = 90,
        frozen: bool = False,
        history_only: bool = False,
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

        if history_only:
            # Stop at the moment the incident would start. Everything up to here
            # is the same for any incident with the same seed, so it is cacheable.
            await self._generate(sc.sim_start - timedelta(days=history_days), sc.incident_start)
            sc.generated_to = sc.incident_start
            await self._save_scenario()
            return sc

        incident = inc.get(incident_key)
        incident.setup(self, sc.incident_start)
        for d in incident.distractors:
            d(self, sc.incident_start)
        await self._flush()

        await self._generate(sc.sim_start - timedelta(days=history_days), sc.sim_start)
        sc.generated_to = sc.sim_start
        if frozen:
            self._frozen_now = sc.sim_start
        await self._save_scenario()
        return sc

    async def activate_incident(
        self, incident_key: str, *, frozen: bool = False, speed: float = 60.0
    ) -> Scenario:
        """Load cached history, plant the incident, and generate its window."""
        async with self.sf() as s:
            row = (
                await s.execute(select(WorldState).where(WorldState.key == "scenario"))
            ).scalar_one()
        sc = Scenario.from_json(row.value)
        sc.incident_key = incident_key
        sc.scenario_id = f"nc-{sc.seed}-{incident_key[:6]}"
        sc.real_start = datetime.utcnow()
        sc.speed = speed
        self.scenario = sc

        incident = inc.get(incident_key)
        incident.setup(self, sc.incident_start)
        for d in incident.distractors:
            d(self, sc.incident_start)
        await self._flush()

        await self._generate(sc.incident_start, sc.sim_start)
        sc.generated_to = sc.sim_start
        if frozen:
            self._frozen_now = sc.sim_start
        await self._save_scenario()
        return sc

    def freeze(self) -> None:
        """Detach the sim clock from wall time.

        Live, sim time runs off the wall clock: time passes while the agent
        thinks, which is realistic and is what makes ``wait_for_traffic``
        honest in the demo.

        Under evaluation that is exactly wrong. It makes the world depend on
        how long the code took to run, so the same seed produces different
        worlds and a slow agent faces a different scenario than a fast one.
        Frozen, the clock moves only when ``advance`` says so, and a seed
        reproduces exactly.
        """
        assert self.scenario is not None
        self._frozen_now = self.now()

    def now(self) -> datetime:
        """Current sim time."""
        assert self.scenario is not None
        if self._frozen_now is not None:
            return self._frozen_now
        elapsed_real = (datetime.utcnow() - self.scenario.real_start).total_seconds()
        return self.scenario.sim_start + timedelta(
            seconds=elapsed_real * self.scenario.speed
        )

    def advance(self, minutes: float) -> None:
        """Jump the sim clock forward without waiting."""
        assert self.scenario is not None
        if self._frozen_now is not None:
            self._frozen_now += timedelta(minutes=minutes)
        else:
            self.scenario.real_start -= timedelta(
                seconds=minutes * 60 / self.scenario.speed
            )

    async def tick(self) -> None:
        """Bring generated data up to the current sim time. Cheap and idempotent."""
        sc = self.scenario
        if sc is None or sc.generated_to is None:
            return
        if self.now() <= sc.generated_to:
            return  # fast path, no lock needed
        async with self._gen_lock:
            # Re-check: another task may have caught up while we waited.
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

            elif action.kind == "issue_goodwill_refunds":
                since = int(action.params.get("since_minutes", 60))
                cutoff = ts - timedelta(minutes=max(1, since))
                rows = (
                    await s.execute(
                        select(Order).where(Order.ts >= cutoff, Order.status == "failed")
                    )
                ).scalars().all()
                value = sum(o.amount_cents for o in rows)
                for o in rows:
                    o.status = "refunded"
                result = {
                    "applied": True,
                    "detail": f"refunded {len(rows)} failed orders ({value / 100:.2f})",
                    "orders_refunded": len(rows),
                    "value_cents": value,
                    "note": "Irreversible. Money has left the account.",
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

        for key, value, desc in CONFIG_KEYS:
            self.set_config(key, value, base, "platform-team", desc)

        # Ordinary configuration churn. Real platforms change settings all day,
        # so "what changed recently?" returns a list, not an answer. Without
        # this the investigation collapses into a single lookup and the agent
        # never has to reason about which change could produce the symptom.
        churn = random.Random(sc.seed ^ 0x5C)
        churnable = [c for c in CONFIG_KEYS if not c[0].startswith("payments.ios")]
        for key, _v, desc in churn.sample(churnable, k=min(9, len(churnable))):
            new_value = churn.choice(CONFIG_CHURN_VALUES.get(key, ["true", "false"]))
            self.set_config(
                key,
                new_value,
                sc.sim_start - timedelta(minutes=churn.randint(20, 20 * 60)),
                churn.choice(["c.rivera", "s.novak", "t.bergman", "platform-team",
                              "release-script/8468", "release-script/8455"]),
                desc,
            )

        for key, enabled, desc in [
            ("checkout.express_wallet", True, "One-tap wallet checkout."),
            ("checkout.address_autocomplete", True, "Address autocomplete in checkout."),
            ("search.semantic_ranking", True, "Semantic ranking in search results."),
            ("risk.strict_velocity_checks", False, "Aggressive velocity fraud checks."),
            ("checkout.strict_address_match", False, "Reject orders whose delivery address does not exactly match the card address."),
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

                # Unrelated traffic movements the incident declares -- a campaign
                # ending really does reduce regional traffic. Real, visible in any
                # "what changed?" sweep, and the cause of nothing.
                drop = incident.region_traffic_drop.get(region)
                if drop and ts >= sc.incident_start - timedelta(hours=2):
                    if rng.random() < drop:
                        continue

                matching = [e for e in effects if e.matches(platform=platform, region=region)]

                added = rng.random() < RATE_CART
                checkout = added and rng.random() < RATE_CHECKOUT

                # Some causes push people out before they ever reach checkout --
                # slow pages, broken search. That shows up in the funnel at a
                # different stage than a payment failure does.
                if checkout:
                    abandon_boost = sum(e.cart_abandon_boost for e in matching)
                    if abandon_boost and rng.random() < abandon_boost:
                        checkout = False

                converted = checkout and rng.random() < RATE_CONVERT
                abandon_stage = None
                incident_hit = False
                hit_effect = None

                if checkout and converted:
                    culprits = [e for e in matching if e.checkout_failure_boost]
                    boost = sum(e.checkout_failure_boost for e in culprits)
                    if boost and rng.random() < boost:
                        converted = False
                        incident_hit = True
                        hit_effect = culprits[0]
                        abandon_stage = hit_effect.abandon_stage
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

                # A payment attempt only exists if the session got as far as
                # paying. Something that loses people at the shipping step
                # leaves no failed payment behind -- which is exactly how the
                # funnel distinguishes the two kinds of cause.
                amount = rng.randint(1800, 24000)
                if converted:
                    o_status, p_status, err = "paid", "success", None
                elif incident_hit:
                    if abandon_stage not in ("payment", "risk_check"):
                        continue
                    code = hit_effect.payment_error_code if hit_effect else None
                    if code is None:
                        continue
                    o_status, p_status, err = "failed", "failed", code
                elif abandon_stage == "payment" and rng.random() < BASELINE_PAYMENT_FAILURE / RATE_CONVERT:
                    o_status, p_status, err = "failed", "failed", _pick(rng, BASELINE_ERROR_CODES)
                else:
                    continue  # abandoned before a payment attempt

                orders.append(
                    {"id": sid, "ts": ts, "session_id": sid, "platform": platform,
                     "region": region, "amount_cents": amount, "status": o_status}
                )
                profile = (
                    hit_effect.payment_profile
                    if (incident_hit and hit_effect and hit_effect.payment_profile)
                    else "standard_v4"
                )
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
                if svc == "payments-svc" and any(
                    e.payment_error_code and e.service is None for e in effects
                ):
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


# ---------------------------------------------------------------------------
# Cached world construction
# ---------------------------------------------------------------------------
#
# Generating a day and a bit of history costs ~40 seconds, which is too slow to
# sit behind a "Start Mission" click and far too slow to run an evaluation batch.
#
# The history *before* the incident does not depend on which incident it is --
# same seed, same rows. So it is generated once, cached as a SQLite file, and
# copied per scenario; only the incident window itself is generated fresh.


import hashlib
import sqlite3
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

# Cloud Run gives a container a read-only image and a writable /tmp, so these
# are configurable. Locally they default to the repo so a rebuild is not needed
# between runs.
CACHE_DIR = Path(os.environ.get("FIXER_CACHE_DIR", ".worldcache"))


def _clone(src: Path, dst: Path) -> None:
    """Copy a SQLite database using SQLite's own backup API.

    A plain file copy is not safe: pages still in the OS write cache, or a
    connection that has not finished closing, produce a file that opens fine
    and then reports "database disk image is malformed" on the first write.
    The backup API takes a consistent snapshot through the engine itself.
    """
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _compact(path: Path) -> None:
    """Reclaim free pages. The cache is copied per mission and shipped in the
    container image, so its size is paid for repeatedly."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def _healthy(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _cache_key(seed: int, sessions_per_min: int, history_days: float, incident_key: str) -> str:
    # An incident may declare unrelated traffic movements that begin before it
    # does, so the history is only shared between incidents that declare the
    # same ones.
    quirks = repr(sorted(inc.get(incident_key).region_traffic_drop.items()))
    raw = f"{seed}|{sessions_per_min}|{history_days}|{quirks}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


async def build_world(
    db_path: str,
    *,
    incident_key: str = "payment_config_regression",
    seed: int = 4242,
    frozen: bool = False,
    speed: float = 60.0,
    sessions_per_min: int = 250,
    history_days: float = 1.3,
    minutes_since_incident: int = 90,
    use_cache: bool = True,
) -> tuple["AsyncEngine", World]:
    """Build a ready-to-run world, reusing cached history where possible."""
    db = Path(db_path)
    if db.exists():
        db.unlink()

    kw = dict(
        incident_key=incident_key,
        seed=seed,
        speed=speed,
        sessions_per_min=sessions_per_min,
        history_days=history_days,
        minutes_since_incident=minutes_since_incident,
    )

    if not use_cache:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        world = World(engine)
        await world.start_scenario(frozen=frozen, **kw)
        return engine, world

    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"history_{_cache_key(seed, sessions_per_min, history_days, incident_key)}.db"

    if cache.exists() and not _healthy(cache):
        # A truncated or half-written cache would poison every run that copies
        # it, and the failure surfaces far from the cause. Rebuild instead.
        cache.unlink()

    if not cache.exists():
        tmp = CACHE_DIR / f"{cache.stem}.building.db"
        if tmp.exists():
            tmp.unlink()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}")
        world = World(engine)
        await world.start_scenario(history_only=True, **kw)
        await engine.dispose()
        _clone(tmp, cache)
        _compact(cache)
        tmp.unlink()

    _clone(cache, db)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    world = World(engine)
    await world.activate_incident(incident_key=incident_key, frozen=frozen, speed=speed)
    return engine, world
