"""Mission state.

A mission is the unit of work: an objective, the evidence gathered for it, the
competing explanations under consideration, the actions taken, and what
verification said afterwards.

Two properties this type is built to guarantee:

* **Reasoning is structured, not prose.** The agent records hypotheses and
  findings through tools, so confidence movements and rejected explanations are
  data. Mission Control renders them and the grader scores them. Nothing here
  exposes raw chain-of-thought -- these are the agent's stated conclusions.
* **Success is a claim that gets checked.** ``conclude`` records what the agent
  asserts. Whether that assertion holds is decided separately, by the grader,
  against the world's real numbers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MissionStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    TIMEOUT = "TIMEOUT"
    SAFETY_LIMIT = "SAFETY_LIMIT"

    @property
    def terminal(self) -> bool:
        return self is not MissionStatus.RUNNING


class HypothesisState(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass
class Hypothesis:
    id: str
    statement: str
    confidence: float  # 0.0 - 1.0
    state: HypothesisState = HypothesisState.OPEN
    history: list[dict] = field(default_factory=list)

    def revise(self, confidence: float, reason: str, state: HypothesisState | None = None) -> None:
        self.history.append(
            {
                "from": self.confidence,
                "to": confidence,
                "reason": reason,
                "at": datetime.utcnow().isoformat(timespec="seconds"),
            }
        )
        self.confidence = confidence
        if state is not None:
            self.state = state

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "confidence": round(self.confidence, 2),
            "state": self.state.value,
            "revisions": len(self.history),
        }


@dataclass
class Finding:
    seq: int
    summary: str
    detail: str
    sim_time: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ActionRecord:
    seq: int
    tool: str
    args: dict
    reason: str
    risk: str
    reversibility: str
    applied: bool
    sim_time: str
    # Filled in once the agent verifies. None means it never checked.
    verified_effective: bool | None = None
    verification_note: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Verification:
    seq: int
    metric: str
    current: float | None
    reference: float | None
    change_pct: float | None
    sufficient_sample: bool
    agent_verdict: str  # "recovered" | "not_recovered" | "inconclusive"
    note: str
    sim_time: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Conclusion:
    status: MissionStatus
    root_cause: str
    evidence_summary: str
    before_after: str
    sim_time: str

    def as_dict(self) -> dict:
        return {**self.__dict__, "status": self.status.value}


@dataclass
class Mission:
    objective: str
    id: str = field(default_factory=lambda: f"m-{uuid.uuid4().hex[:8]}")
    success_criteria: list[str] = field(default_factory=list)
    status: MissionStatus = MissionStatus.RUNNING
    hypotheses: list[Hypothesis] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    verifications: list[Verification] = field(default_factory=list)
    conclusion: Conclusion | None = None
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds"))
    ended_at: str | None = None

    # -- reasoning ----------------------------------------------------------

    def add_hypothesis(self, statement: str, confidence: float) -> Hypothesis:
        h = Hypothesis(
            id=f"H{len(self.hypotheses) + 1}",
            statement=statement,
            confidence=_clamp(confidence),
        )
        self.hypotheses.append(h)
        return h

    def hypothesis(self, hid: str) -> Hypothesis | None:
        return next((h for h in self.hypotheses if h.id.lower() == hid.lower()), None)

    def add_finding(self, summary: str, detail: str, sim_time: str) -> Finding:
        f = Finding(len(self.findings) + 1, summary, detail, sim_time)
        self.findings.append(f)
        return f

    # -- doing --------------------------------------------------------------

    def record_action(
        self, tool: str, args: dict, risk: str, reversibility: str, applied: bool, sim_time: str
    ) -> ActionRecord:
        a = ActionRecord(
            seq=len(self.actions) + 1,
            tool=tool,
            args={k: v for k, v in args.items() if k != "reason"},
            reason=str(args.get("reason", "")),
            risk=risk,
            reversibility=reversibility,
            applied=applied,
            sim_time=sim_time,
        )
        self.actions.append(a)
        return a

    def record_verification(self, **kw) -> Verification:
        v = Verification(seq=len(self.verifications) + 1, **kw)
        self.verifications.append(v)
        # A verification always lands on the most recent action that has not
        # been judged yet -- that is the thing being tested.
        pending = next((a for a in reversed(self.actions) if a.verified_effective is None), None)
        if pending is not None and v.agent_verdict in ("recovered", "not_recovered"):
            pending.verified_effective = v.agent_verdict == "recovered"
            pending.verification_note = v.note
        return v

    def conclude(
        self, status: MissionStatus, root_cause: str, evidence_summary: str,
        before_after: str, sim_time: str,
    ) -> Conclusion:
        self.conclusion = Conclusion(status, root_cause, evidence_summary, before_after, sim_time)
        self.status = status
        self.ended_at = datetime.utcnow().isoformat(timespec="seconds")
        return self.conclusion

    # -- views --------------------------------------------------------------

    @property
    def failed_actions(self) -> list[ActionRecord]:
        """Actions that ran but were then shown not to fix the problem."""
        return [a for a in self.actions if a.applied and a.verified_effective is False]

    @property
    def recovered_after_failure(self) -> bool:
        """Did a remediation fail verification and a later one succeed?"""
        seen_failure = False
        for a in self.actions:
            if a.verified_effective is False:
                seen_failure = True
            elif seen_failure and a.verified_effective is True:
                return True
        return False

    @property
    def leading_hypothesis(self) -> Hypothesis | None:
        live = [h for h in self.hypotheses if h.state is not HypothesisState.REJECTED]
        return max(live, key=lambda h: h.confidence, default=None)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "status": self.status.value,
            "success_criteria": self.success_criteria,
            "hypotheses": [h.as_dict() for h in self.hypotheses],
            "findings": [f.as_dict() for f in self.findings],
            "actions": [a.as_dict() for a in self.actions],
            "verifications": [v.as_dict() for v in self.verifications],
            "conclusion": self.conclusion.as_dict() if self.conclusion else None,
            "counters": {
                "findings": len(self.findings),
                "hypotheses": len(self.hypotheses),
                "actions_taken": len([a for a in self.actions if a.applied]),
                "failed_remediations": len(self.failed_actions),
                "verifications": len(self.verifications),
                "recovered_after_failure": self.recovered_after_failure,
            },
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))
