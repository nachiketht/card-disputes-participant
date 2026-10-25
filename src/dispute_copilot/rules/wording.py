"""Phrase lists and quote openings. The check order stays in code.

The file is loaded from the package root. A duplicate id, an unknown type, or
an unknown missing-field id fails before a case runs. Quote openings are
checked against the loaded procedure, and each opening must start exactly one
line.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from dispute_copilot.fields import Classification, MissingId

_ROOT = Path(__file__).resolve().parents[3]
_RULES = _ROOT / "config" / "rules" / "decision_table.yaml"
_EXTRA_OPENINGS = frozenset({"routing_line", "service_outage"})
_PHRASE_IDS = frozenset(
    {
        Classification.UNSUPPORTED.value,
        "recognition",
        "duplicate",
        "service_gap",
    }
)
_CACHED: "Wording | None" = None


@dataclass(frozen=True)
class Wording:
    phrases: dict[str, tuple[str, ...]]
    openings: dict[str, str]
    missing_openings: dict[str, str]

    def phrases_for(self, phrase_id: str) -> tuple[str, ...]:
        return self.phrases[phrase_id]

    def type_prefix(self, classification: Classification) -> str:
        return self.openings[classification.value]

    def missing_prefix(self, field: MissingId) -> str:
        return self.missing_openings[field.value]

    def prefixes(self) -> tuple[str, ...]:
        return tuple(self.openings.values()) + tuple(self.missing_openings.values())


def load_wording(path: Path | None = None) -> Wording:
    global _CACHED
    if path is None and _CACHED is not None:
        return _CACHED
    source = path or _RULES
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    wording = parse_wording(data)
    if path is None:
        _CACHED = wording
    return wording


def parse_wording(data: object) -> Wording:
    if not isinstance(data, dict):
        raise ValueError("decision table must be a mapping")
    phrases = _phrase_rows(data.get("phrases"), "phrases")
    openings = _prefix_rows(data.get("openings"), "openings", _opening_ids())
    missing = _prefix_rows(data.get("missing_openings"), "missing_openings", {item.value for item in MissingId})
    return Wording(phrases, openings, missing)


def validate_openings(wording: Wording, procedure_text: str) -> None:
    labeled = list(wording.openings.items()) + list(wording.missing_openings.items())
    for row_id, prefix in labeled:
        matches = [line for line in procedure_text.splitlines() if line.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f"{row_id} opening matches {len(matches)} lines")


def procedure_stub(source_id: str) -> str:
    """A procedure whose every configured opening starts exactly one line."""
    lines = [f"Source ID: {source_id}"]
    for prefix in load_wording().prefixes():
        lines.append(f"{prefix} sample")
    return "\n".join(lines) + "\n"


def _opening_ids() -> set[str]:
    return {item.value for item in Classification} | set(_EXTRA_OPENINGS)


def _phrase_rows(rows: object, label: str) -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for row_id, values in _rows(rows, label):
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
            raise ValueError(f"{row_id} has no phrase list")
        found[row_id] = tuple(values)
    if set(found) != _PHRASE_IDS:
        _unknown_or_missing(set(found), _PHRASE_IDS, "phrase")
    return found


def _prefix_rows(rows: object, label: str, expected: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for row_id, prefix in _rows(rows, label):
        if not isinstance(prefix, str) or not prefix:
            raise ValueError(f"{row_id} has no opening")
        found[row_id] = prefix
    if set(found) != expected:
        kind = "missing-field" if label == "missing_openings" else "type"
        _unknown_or_missing(set(found), expected, kind)
    return found


def _rows(rows: object, label: str):
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} row is not a mapping")
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise ValueError(f"{label} row has no id")
        if row_id in seen:
            raise ValueError(f"duplicate row id {row_id}")
        seen.add(row_id)
        yield row_id, row.get("values", row.get("prefix"))


def _unknown_or_missing(found: set[str], expected: set[str], kind: str) -> None:
    unknown = sorted(found - expected)
    if unknown:
        raise ValueError(f"unknown {kind} id {unknown[0]}")
    missing = sorted(expected - found)
    raise ValueError(f"missing {kind} id {missing[0]}")
