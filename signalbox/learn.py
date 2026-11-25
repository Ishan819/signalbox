"""Learn the score weights from your own labels instead of the hand-set formula.

A tiny logistic regression in pure Python (no numpy). It is trained on the three signals and
their interactions and scored with k-fold cross-validation, so the reported numbers always come
from messages the model never saw.
"""
from __future__ import annotations

import math
import random

from .scorer import Triage

FEATURES = ["bias", "human", "relationship", "ask", "human×ask", "human×relationship", "human×ask×relationship"]


def features(t: Triage) -> list[float]:
    h, r, k = 1 - t.automation.score, t.relationship.score, t.ask.score
    return [1.0, h, r, k, h * k, h * r, h * k * r]


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z > -60 else 0.0


class LogisticModel:
    def __init__(self, weights: list[float] | None = None):
        self.w = weights or [0.0] * len(FEATURES)

    def predict(self, x: list[float]) -> float:
        return _sigmoid(sum(wi * xi for wi, xi in zip(self.w, x)))

    def fit(self, X: list[list[float]], y: list[bool], epochs: int = 1500, lr: float = 0.5,
            l2: float = 0.01) -> "LogisticModel":
        pos = sum(y) or 1
        neg = (len(y) - sum(y)) or 1
        weight = {True: len(y) / (2 * pos), False: len(y) / (2 * neg)}   # important messages are rare
        n = len(X)
        for _ in range(epochs):
            grad = [0.0] * len(self.w)
            for x, label in zip(X, y):
                err = (self.predict(x) - label) * weight[label]
                for j, xj in enumerate(x):
                    grad[j] += err * xj
            for j in range(len(self.w)):
                reg = l2 * self.w[j] if j else 0.0
                self.w[j] -= lr * (grad[j] / n + reg)
        return self

    def explain(self) -> str:
        return "\n".join(f"  {name:<26} {w:+.2f}" for name, w in zip(FEATURES, self.w))


def cross_validated_scores(results: list[Triage], k: int = 4, seed: int = 0) -> dict[str, float]:
    """Held-out probability for every labeled message (stratified k-fold)."""
    labeled = [t for t in results if t.message.label is not None]
    rng = random.Random(seed)
    pos = [t for t in labeled if t.message.label]
    neg = [t for t in labeled if not t.message.label]
    rng.shuffle(pos)
    rng.shuffle(neg)
    folds = [pos[i::k] + neg[i::k] for i in range(k)]
    out: dict[str, float] = {}
    for i, test in enumerate(folds):
        train = [t for j, f in enumerate(folds) if j != i for t in f]
        model = LogisticModel().fit([features(t) for t in train], [bool(t.message.label) for t in train])
        for t in test:
            out[t.message.id] = model.predict(features(t))
    return out


def fit_all(results: list[Triage]) -> LogisticModel:
    labeled = [t for t in results if t.message.label is not None]
    return LogisticModel().fit([features(t) for t in labeled], [bool(t.message.label) for t in labeled])
