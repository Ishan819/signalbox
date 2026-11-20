"""Combine the three signals into one priority score with a human-readable explanation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .ask import AskResult, HeuristicAskDetector
from .automation import AutomationAnalyzer, AutomationResult
from .models import Identity, Message
from .relationship import RelationshipGraph, RelationshipResult

MODES = ("headers", "headers+relationship", "full")
NEEDS_YOU, WORTH_A_LOOK = 0.5, 0.22


@dataclass
class Triage:
    message: Message
    score: float
    tier: str                               # needs_you | worth_a_look | low
    why: str                                # one-line explanation shown in the UI
    automation: AutomationResult
    relationship: RelationshipResult
    ask: AskResult
    signals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "message": self.message.to_dict(), "score": round(self.score, 4), "tier": self.tier,
            "why": self.why,
            "signals": {
                "automation": {"score": round(self.automation.score, 3), "reasons": self.automation.reasons},
                "relationship": {"score": round(self.relationship.score, 3), "reasons": self.relationship.reasons},
                "ask": {"score": round(self.ask.score, 3), "reasons": self.ask.reasons,
                        "deadline": self.ask.deadline, "summary": self.ask.summary},
            },
        }


def combine(mode: str, automation: float, relationship: float, ask: float) -> float:
    human = (1 - automation) ** 1.5         # a strong automation signal should dominate
    if mode == "headers":
        return human
    if mode == "headers+relationship":
        return human * (0.3 + 0.7 * relationship)
    if mode == "full":
        return human * (0.12 + 0.53 * ask + 0.35 * relationship)
    raise ValueError(f"mode must be one of {MODES}")


def explain(m: Message, a: AutomationResult, r: RelationshipResult, k: AskResult, tier: str) -> str:
    who = m.sender_name or m.sender
    if tier == "low" and a.score >= 0.5:
        return f"Automated — {a.reasons[0]}" if a.reasons else "Automated"
    bits = [f"{who} ({r.reasons[0]})"]
    bits += k.reasons[:3]
    if tier == "low" and not k.reasons:
        bits.append("no ask for you")
    return " · ".join(bits)


class Triager:
    def __init__(self, me: Identity, messages: list[Message], mode: str = "full",
                 ask_detector=None, now: datetime | None = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.me, self.messages, self.mode = me, messages, mode
        self.automation = AutomationAnalyzer(me, messages)
        self.graph = RelationshipGraph(me, messages, now=now)
        self.ask = ask_detector or HeuristicAskDetector()

    def inbound(self) -> list[Message]:
        return [m for m in self.messages if not self.me.is_me(m.sender)]

    def score_one(self, m: Message) -> Triage:
        a = self.automation.analyze(m)
        r = self.graph.analyze(m)
        # Only spend LLM calls on messages that are not already obviously machine-sent.
        if self.mode == "full" and (a.score < 0.7 or self.ask.name == "heuristic"):
            k = self.ask.detect(m, self.me)
        elif self.mode == "full":
            k = HeuristicAskDetector().detect(m, self.me)
        else:
            k = AskResult(0.0)
        s = combine(self.mode, a.score, r.score, k.score)
        tier = "needs_you" if s >= NEEDS_YOU else "worth_a_look" if s >= WORTH_A_LOOK else "low"
        return Triage(m, s, tier, explain(m, a, r, k, tier), a, r, k)

    def triage(self) -> list[Triage]:
        results = [self.score_one(m) for m in self.inbound()]
        results.sort(key=lambda t: (-t.score, -t.message.timestamp.timestamp()))
        return results
