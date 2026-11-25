"""Measure triage quality on labeled messages, and compare signal combinations (ablation)."""
from __future__ import annotations

from .models import Identity, Message
from .scorer import MODES, NEEDS_YOU, Triager


def metrics(ranked_labels: list[bool], flagged: list[bool], ks=(2, 5, 10)) -> dict:
    total = sum(ranked_labels)
    out: dict = {"positives": total, "messages": len(ranked_labels)}
    for k in ks:
        top = ranked_labels[:k]
        out[f"p@{k}"] = sum(top) / k
        out[f"r@{k}"] = sum(top) / total if total else 0.0
    # average precision: rewards putting every important message near the top
    hits, ap = 0, 0.0
    for i, lab in enumerate(ranked_labels, 1):
        if lab:
            hits += 1
            ap += hits / i
    out["avg_precision"] = ap / total if total else 0.0
    tp = sum(f and l for f, l in zip(flagged, ranked_labels))
    fp = sum(f and not l for f, l in zip(flagged, ranked_labels))
    fn = sum(l and not f for f, l in zip(flagged, ranked_labels))
    out["flag_precision"] = tp / (tp + fp) if tp + fp else 0.0
    out["flag_recall"] = tp / (tp + fn) if tp + fn else 0.0
    return out


def evaluate(me: Identity, messages: list[Message], modes=MODES, ask_detector=None) -> dict[str, dict]:
    results = {}
    for mode in modes:
        ranked = [t for t in Triager(me, messages, mode=mode, ask_detector=ask_detector).triage()
                  if t.message.label is not None]
        # Pessimistic tie-breaking: among equal scores, unimportant messages rank first, so a
        # mode can't look good just because ties happened to fall in its favour.
        ranked.sort(key=lambda t: (-round(t.score, 6), bool(t.message.label)))
        results[mode] = metrics([bool(t.message.label) for t in ranked],
                                [t.score >= NEEDS_YOU for t in ranked])
        results[mode]["misses"] = [t for t in ranked if t.message.label and t.score < NEEDS_YOU]
        results[mode]["false_alarms"] = [t for t in ranked if not t.message.label and t.score >= NEEDS_YOU]
    return results


def format_table(results: dict[str, dict]) -> str:
    cols = ["p@2", "r@5", "r@10", "avg_precision", "flag_precision", "flag_recall"]
    head = f"{'signals used':<24}" + "".join(f"{c:>15}" for c in cols)
    lines = [head, "-" * len(head)]
    for mode, r in results.items():
        lines.append(f"{mode:<24}" + "".join(f"{r[c]:>15.2f}" for c in cols))
    any_r = next(iter(results.values()))
    lines.append(f"\n{any_r['positives']} important messages out of {any_r['messages']} inbound "
                 f"({any_r['positives'] / max(1, any_r['messages']):.0%})")
    return "\n".join(lines)


def evaluate_learned(me: Identity, messages: list[Message], ask_detector=None, k: int = 4) -> dict:
    """Same metrics, but scores come from a logistic model scored with k-fold cross-validation."""
    from .learn import cross_validated_scores
    results = Triager(me, messages, mode="full", ask_detector=ask_detector).triage()
    probs = cross_validated_scores(results, k=k)
    ranked = sorted((t for t in results if t.message.id in probs),
                    key=lambda t: (-round(probs[t.message.id], 6), bool(t.message.label)))
    out = metrics([bool(t.message.label) for t in ranked], [probs[t.message.id] >= 0.5 for t in ranked])
    out["misses"] = [t for t in ranked if t.message.label and probs[t.message.id] < 0.5]
    out["false_alarms"] = [t for t in ranked if not t.message.label and probs[t.message.id] >= 0.5]
    return out


def best_thresholds(scores: list[float], labels: list[bool]) -> dict[str, tuple[float, float, float]]:
    """Candidate cut-offs for the needs_you tier: (threshold, precision, recall).

    * best_f1         -- balances missed messages against false alarms
    * catch_everything -- highest threshold that still flags every important message
    """
    total = sum(labels)
    best_f1, catch_all = (0.0, 0.0, 0.0, -1.0), None
    for th in sorted(set(scores), reverse=True):
        flagged = [l for s, l in zip(scores, labels) if s >= th]
        tp = sum(flagged)
        p = tp / len(flagged) if flagged else 0.0
        r = tp / total if total else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if f1 > best_f1[3]:
            best_f1 = (th, p, r, f1)
        if catch_all is None and r == 1.0:
            catch_all = (th, p, r)
    return {"best_f1": best_f1[:3], "catch_everything": catch_all or (0.0, 0.0, 0.0)}
