"""A short daily digest: only what needs you, plus one line on what was filtered out."""
from __future__ import annotations

from collections import Counter

from .scorer import Triage


def _bucket(t: Triage) -> str:
    reasons = " ".join(t.automation.reasons).lower()
    if "bot account" in reasons:
        return "bot posts"
    if "mailing-list" in reasons or "bulk" in reasons or "precedence" in reasons:
        return "newsletters & lists"
    if "auto-submitted" in reasons or "automated sender" in reasons:
        return "notifications"
    if "template" in reasons or "ai agent" in reasons:
        return "AI / templated outreach"
    return "low-priority human messages"


def build_digest(results: list[Triage], name: str = "") -> str:
    needs = [t for t in results if t.tier == "needs_you"]
    look = [t for t in results if t.tier == "worth_a_look"]
    low = [t for t in results if t.tier == "low"]
    hello = f"Hi {name.split()[0]}, " if name else ""
    lines = [f"# {hello}{len(needs)} message{'s' if len(needs) != 1 else ''} need you", ""]
    for t in needs:
        m = t.message
        deadline = f" — **due {t.ask.deadline}**" if t.ask.deadline else ""
        lines.append(f"- **{m.sender_name or m.sender}**: {m.subject or m.body[:60]}{deadline}")
        lines.append(f"  - {t.why}")
    if look:
        lines += ["", f"**Worth a look ({len(look)}):** " + ", ".join(
            (t.message.sender_name or t.message.sender) for t in look[:8]) + ("…" if len(look) > 8 else "")]
    if low:
        counts = Counter(_bucket(t) for t in low)
        lines += ["", f"Filtered out {len(low)}: " + ", ".join(f"{n} {k}" for k, n in counts.most_common())]
    return "\n".join(lines)
