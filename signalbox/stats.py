"""Inbox health: who floods you, and which automation signals fire most (good unsubscribe targets)."""
from __future__ import annotations

from collections import Counter, defaultdict

from .scorer import Triage


def inbox_stats(results: list[Triage], top: int = 10) -> str:
    tiers = Counter(t.tier for t in results)
    total = len(results) or 1
    lines = [f"{len(results)} inbound messages: "
             f"{tiers['needs_you']} need you, {tiers['worth_a_look']} worth a look, {tiers['low']} filtered "
             f"({tiers['low'] / total:.0%} noise)", ""]

    senders: dict[str, list[Triage]] = defaultdict(list)
    for t in results:
        if t.automation.score >= 0.5:
            senders[t.message.sender].append(t)
    lines.append(f"Top automated senders (unsubscribe / mute candidates):")
    for sender, ts in sorted(senders.items(), key=lambda kv: -len(kv[1]))[:top]:
        name = ts[0].message.sender_name or sender
        lines.append(f"  {len(ts):>4}  {name[:28]:<28} {sender}")

    reasons = Counter(r.split(" (")[0] for t in results for r in t.automation.reasons)
    lines += ["", "Automation signals that fired:"]
    lines += [f"  {n:>4}  {r}" for r, n in reasons.most_common(top)]
    return "\n".join(lines)
