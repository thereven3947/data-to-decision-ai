"""Classifier eval on the frozen golden set: per-class precision of non-abstained predictions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from d2d.hitl.training_set import Example
from d2d.ml.train import Classifier


@dataclass(frozen=True)
class ClassifierEval:
    examples: int
    covered: int
    correct: int
    precision_by_class: dict[str, float] = field(default_factory=dict)
    errors: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.covered / self.examples if self.examples else 1.0

    @property
    def min_precision(self) -> float:
        return min(self.precision_by_class.values(), default=1.0)


def evaluate_classifier(classifier: Classifier, golden: list[Example]) -> ClassifierEval:
    predictions = classifier.predict(golden)
    predicted: Counter[str] = Counter()
    right: Counter[str] = Counter()
    errors = []
    for ex, p in zip(golden, predictions, strict=True):
        if p.abstain:
            continue
        predicted[p.category] += 1
        if p.category == ex["category"]:
            right[p.category] += 1
        else:
            errors.append((ex["story_id"], ex["category"], p.category))
    return ClassifierEval(
        examples=len(golden),
        covered=sum(predicted.values()),
        correct=sum(right.values()),
        precision_by_class={c: right[c] / n for c, n in sorted(predicted.items())},
        errors=errors,
    )
