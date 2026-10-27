"""Deterministic assignment from a seeded hash of the case id."""

import hashlib

from dispute_copilot.classifiers.config import ClassifierSettings


def assign_approach(case_id: str, settings: ClassifierSettings) -> str:
    digest = hashlib.sha256(f"{settings.ab_seed}:{case_id}".encode("utf-8")).digest()
    point = int.from_bytes(digest[:8], "big") / float(2**64)
    keys = ("1", "2", "3")
    total = sum(max(0.0, settings.ab_weights.get(key, 0.0)) for key in keys)
    if total <= 0:
        return "1"
    cursor = 0.0
    for key in keys:
        cursor += max(0.0, settings.ab_weights.get(key, 0.0)) / total
        if point < cursor:
            return key
    return "3"
