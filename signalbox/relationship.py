"""Signal 2: do you actually have a relationship with this sender?

Built only from *your own behaviour* (who you write to, how often, how recently, which
threads you are in) -- the one thing an AI agent blasting your inbox cannot fake.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .models import Identity, Message

PUBLIC_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
                  "proton.me", "protonmail.com", "aol.com", "live.com", "me.com"}


@dataclass
class RelationshipResult:
    score: float                     # 0 = stranger, 1 = close, active relationship
    reasons: list[str] = field(default_factory=list)


def _domain(addr: str) -> str | None:
    return addr.rsplit("@", 1)[1] if "@" in addr else None


def _ago(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)}d ago"


class RelationshipGraph:
    def __init__(self, me: Identity, messages: list[Message], now: datetime | None = None,
                 half_life_days: float = 45.0):
        self.me = me
        self.now = now or max((m.timestamp for m in messages), default=datetime.now().astimezone())
        self.half_life = half_life_days
        self.sent_to: dict[str, list[datetime]] = defaultdict(list)
        self.domain_contacts: dict[str, set[str]] = defaultdict(set)
        self.my_threads: set[str] = set()

        for m in messages:
            if not me.is_me(m.sender):
                continue
            if m.thread_id:
                self.my_threads.add(m.thread_id)
            for r in set(m.recipients + m.cc):
                if me.is_me(r):
                    continue
                self.sent_to[r].append(m.timestamp)
                d = _domain(r)
                if d and d not in PUBLIC_DOMAINS:
                    self.domain_contacts[d].add(r)

    def _person_strength(self, who: str) -> tuple[float, int, float]:
        stamps = self.sent_to.get(who)
        if not stamps:
            return 0.0, 0, math.inf
        n = len(stamps)
        days = max(0.0, (self.now - max(stamps)).total_seconds() / 86400)
        volume = 1 - math.exp(-n / 3)                      # 1 msg ~0.28, 3 ~0.63, 10 ~0.96
        recency = 0.5 ** (days / self.half_life)
        return volume * (0.5 + 0.5 * recency), n, days

    def analyze(self, m: Message) -> RelationshipResult:
        score, n, days = self._person_strength(m.sender)
        reasons: list[str] = []
        if n:
            reasons.append(f"you've written to them {n}× (last {_ago(days)})")
        else:
            d = _domain(m.sender)
            colleagues = self.domain_contacts.get(d, set()) if d else set()
            if colleagues:
                score = max(score, 0.3)
                reasons.append(f"same org as people you write to (@{d})")

        if m.thread_id and m.thread_id in self.my_threads:
            score = 1 - (1 - score) * 0.4
            reasons.append("reply in a thread you're part of")
        if not reasons:
            reasons.append("no history with this sender")
        return RelationshipResult(min(1.0, score), reasons)
