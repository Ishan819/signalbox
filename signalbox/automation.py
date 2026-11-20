"""Signal 1: how likely is it that a machine (bot, AI agent, bulk sender) produced this message?

Deliberately does NOT try to detect "AI-written prose" -- that is unreliable and a real
person may well use AI to draft. Instead it uses things machines can't easily fake away:
transport metadata, platform flags, sending patterns and templated content.
"""
from __future__ import annotations

import re
import statistics
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

from .models import Identity, Message

NOREPLY = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|mailer-daemon|bounces?|"
    r"bot|automated|updates|news(letter)?|digest|marketing|hello|team|info|calendar-notification)"
    r"([+._-][^@]*)?@",
    re.I,
)
AI_DISCLOSURE = re.compile(
    r"(\bI(?:'| a)m an? (?:AI|automated|virtual|digital)\b"
    r"|\b(?:AI|virtual) (?:assistant|agent|SDR)\b"
    r"|this (?:message|email|summary) was (?:automatically )?(?:generated|sent|created)"
    r"|\bautomated (?:message|notification|reminder)\b"
    r"|\bdo not reply\b|\bAI[- ]generated\b|\bunsubscribe\b)",
    re.I,
)
BULK_PRECEDENCE = {"bulk", "list", "junk", "auto_reply"}


@dataclass
class AutomationResult:
    score: float                       # 0 = surely human, 1 = surely automated
    reasons: list[str] = field(default_factory=list)


def noisy_or(probs: list[float]) -> float:
    out = 1.0
    for p in probs:
        out *= 1.0 - max(0.0, min(1.0, p))
    return 1.0 - out


# --------------------------------------------------------- template detection
_TOKEN = re.compile(r"[a-z']+|\d+")


def _normalize(text: str, me: Identity) -> list[str]:
    text = text.lower()
    text = re.sub(r"https?://\S+", " url ", text)
    text = re.sub(r"\S+@\S+", " email ", text)
    if me.first_name:
        text = text.replace(me.first_name.lower(), " name ")
    return ["num" if t.isdigit() else t for t in _TOKEN.findall(text)]


def _shingles(tokens: list[str], k: int = 3) -> set[str]:
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


class NearDuplicateIndex:
    """MinHash + LSH banding so template detection stays ~linear on big inboxes."""

    def __init__(self, num_perm: int = 48, bands: int = 16, threshold: float = 0.45):
        self.num_perm, self.bands, self.threshold = num_perm, bands, threshold
        self.rows = num_perm // bands
        self.salts = [i.to_bytes(4, "little") for i in range(num_perm)]

    def _signature(self, shingles: set[str]) -> tuple[int, ...]:
        encoded = [s.encode() for s in shingles]
        return tuple(min(zlib.crc32(salt + e) for e in encoded) for salt in self.salts)

    def duplicate_counts(self, docs: dict[str, set[str]], group: dict[str, str]) -> dict[str, int]:
        """For each doc id: how many *other* docs (outside its own thread/group) are near-copies."""
        buckets: dict[tuple, list[str]] = defaultdict(list)
        for doc_id, sh in docs.items():
            if len(sh) < 4:           # too short to call a template
                continue
            sig = self._signature(sh)
            for b in range(self.bands):
                buckets[(b, sig[b * self.rows:(b + 1) * self.rows])].append(doc_id)

        neighbours: dict[str, set[str]] = defaultdict(set)
        checked: set[tuple[str, str]] = set()
        for ids in buckets.values():
            if len(ids) < 2 or len(ids) > 500:
                continue
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    pair = (a, b) if a < b else (b, a)
                    if pair in checked or group.get(a) == group.get(b):
                        continue
                    checked.add(pair)
                    sa, sb = docs[a], docs[b]
                    if len(sa & sb) / len(sa | sb) >= self.threshold:
                        neighbours[a].add(b)
                        neighbours[b].add(a)
        return {doc_id: len(neighbours.get(doc_id, ())) for doc_id in docs}


# ------------------------------------------------------------------ analyzer
class AutomationAnalyzer:
    def __init__(self, me: Identity, messages: list[Message]):
        self.me = me
        inbound = [m for m in messages if not me.is_me(m.sender)]
        written_to = {r for m in messages if me.is_me(m.sender) for r in m.recipients + m.cc}

        # per-sender volume + cadence
        by_sender: dict[str, list[Message]] = defaultdict(list)
        for m in inbound:
            by_sender[m.sender].append(m)
        self.sender_volume = {s: len(ms) for s, ms in by_sender.items()}
        self.sender_regular: dict[str, float] = {}
        for s, ms in by_sender.items():
            if len(ms) >= 4:
                ts = sorted(m.timestamp.timestamp() for m in ms)
                gaps = [b - a for a, b in zip(ts, ts[1:])]
                mean = statistics.mean(gaps)
                if mean > 0:
                    self.sender_regular[s] = statistics.pstdev(gaps) / mean   # coefficient of variation
        self.one_way = {s for s in by_sender if s not in written_to}

        # cross-inbox templates
        docs = {m.id: _shingles(_normalize(m.body, me)) for m in inbound}
        groups = {m.id: m.thread_id or m.id for m in inbound}
        self.dup_counts = NearDuplicateIndex().duplicate_counts(docs, groups)

    def analyze(self, m: Message) -> AutomationResult:
        probs: list[float] = []
        reasons: list[str] = []

        def hit(p: float, why: str) -> None:
            probs.append(p)
            reasons.append(why)

        h = m.headers
        if m.is_bot:
            hit(0.97, "posted by a bot account")
        if "list-unsubscribe" in h or "list-id" in h:
            hit(0.9, "mailing-list headers (List-Unsubscribe / List-Id)")
        if h.get("auto-submitted", "no").lower() != "no":
            hit(0.95, f"Auto-Submitted: {h['auto-submitted']}")
        if h.get("precedence", "").lower() in BULK_PRECEDENCE:
            hit(0.9, f"Precedence: {h['precedence']}")
        if any(k.startswith(("x-campaign", "x-mailgun", "x-sg-", "x-ses", "x-mc-", "feedback-id")) for k in h):
            hit(0.75, "sent through a bulk-mail platform")
        if NOREPLY.match(m.sender):
            hit(0.85, f"automated sender address ({m.sender.split('@')[0]}@)")
        if AI_DISCLOSURE.search(m.text):
            hit(0.8, "self-identifies as automated / AI agent")

        dups = self.dup_counts.get(m.id, 0)
        if dups >= 2:
            hit(min(0.9, 0.55 + 0.05 * dups), f"near-identical to {dups} other messages (template)")
        cv = self.sender_regular.get(m.sender)
        if cv is not None and cv < 0.3:
            hit(0.55, "sender posts on a clockwork schedule")
        vol = self.sender_volume.get(m.sender, 0)
        if m.sender in self.one_way and vol >= 5:
            hit(0.45, f"one-way high-volume sender ({vol} msgs, you never wrote back)")

        return AutomationResult(noisy_or(probs), reasons)
