"""Mask restricted values before a draft, log, trace, or report is written.

Markers and patterns come from config/rules/redaction.yaml. The built-in
customer-reference and token markers are always included, even when the file
adds more. A quote that still contains a restricted value after masking is not
safe to store.
"""

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from dispute_copilot.errors import LeakError

MASK = "[redacted]"
_TRAILING = r"[A-Za-z0-9_\-]*"
_ROOT = Path(__file__).resolve().parents[3]
REDACTION_FILE = _ROOT / "config" / "rules" / "redaction.yaml"
_REQUIRED = ("markers", "card", "iban", "account", "email", "phone")
_ALLOWED_KEYS = frozenset({"enabled", "markers", "card", "patterns", "extra_markers", "extra_patterns"})
_PATTERN_KEYS = frozenset({"email", "phone", "account", "iban"})
_ID_KEYS = frozenset({"span_id", "trace_id", "parent_id", "run_id"})
_ID_PREFIX = re.compile(r'(?:span_id|trace_id|parent_id)"\s*:\s*"$')
_HEX_ID = re.compile(r"[0-9a-fA-F]{8,64}")
_CARD = re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]?){12,18}\d(?![A-Za-z0-9])")
_DEFAULT_MARKERS = ("SYNTHETIC-" + "PRIVATE", "private_" + "token", "CUST" + "-")
_DEFAULT_PATTERNS = {
    "email": r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    "phone": r"(?<!\d)(?:\+\d{8,15}|\+\d{1,3}(?:[\s.\-]\d{2,4}){2,3}|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)(?!\d)",
    "account": r"(?i)\b(?:account|acct|a/c)\b[\s:#\-]*\d{8,17}\b",
    "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
}


@dataclass(frozen=True)
class RedactionRules:
    markers: tuple[str, ...]
    patterns: dict[str, str]
    extra_patterns: tuple[str, ...]


def _marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(re.escape(marker) + _TRAILING)


def load_redaction(path: Path | None = None) -> RedactionRules:
    return _load_cached(str(path or REDACTION_FILE))


@functools.lru_cache(maxsize=8)
def _load_cached(path: str) -> RedactionRules:
    file = Path(path)
    if not file.is_file():
        return RedactionRules(_DEFAULT_MARKERS, dict(_DEFAULT_PATTERNS), ())
    loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{file.name} must be a mapping")
    unknown = set(loaded) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{file.name} has unknown keys: {sorted(unknown)}")
    enabled = loaded.get("enabled")
    if not isinstance(enabled, list) or any(name not in enabled for name in _REQUIRED):
        raise ValueError(f"{file.name} must enable markers, card, iban, account, email, and phone")
    if loaded.get("card") != "luhn":
        raise ValueError(f"{file.name} card detection must stay luhn")
    raw_markers = loaded.get("markers") or []
    extra_markers = loaded.get("extra_markers") or []
    if not isinstance(raw_markers, list) or not isinstance(extra_markers, list):
        raise ValueError(f"{file.name} markers must be lists")
    markers = tuple(dict.fromkeys([*_DEFAULT_MARKERS, *[str(item) for item in raw_markers], *[str(item) for item in extra_markers]]))
    file_patterns = loaded.get("patterns") or {}
    if not isinstance(file_patterns, dict):
        raise ValueError(f"{file.name} patterns must be a mapping")
    unknown_patterns = set(file_patterns) - _PATTERN_KEYS
    if unknown_patterns:
        raise ValueError(f"{file.name} has unknown patterns: {sorted(unknown_patterns)}")
    patterns = dict(_DEFAULT_PATTERNS)
    for key, value in file_patterns.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{file.name} pattern {key} must be a regular expression")
        re.compile(value)
        patterns[key] = value
    extra = loaded.get("extra_patterns") or []
    if not isinstance(extra, list) or any(not isinstance(item, str) or not item.strip() for item in extra):
        raise ValueError(f"{file.name} extra_patterns must be regular expressions")
    for item in extra:
        re.compile(item)
    return RedactionRules(markers, patterns, tuple(extra))


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = ord(char) - 48
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _iban_ok(token: str) -> bool:
    if not (15 <= len(token) <= 34):
        return False
    rearranged = token[4:] + token[:4]
    digits = []
    for char in rearranged:
        if char.isdigit():
            digits.append(char)
        elif char.isalpha():
            digits.append(str(ord(char.upper()) - 55))
        else:
            return False
    return int("".join(digits)) % 97 == 1


def _id_context(text: str, start: int) -> bool:
    return _ID_PREFIX.search(text[max(0, start - 40) : start]) is not None


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


class Redactor:
    def __init__(self, path: Path | None = None) -> None:
        self.rules = load_redaction(path)
        self._markers = tuple(_marker_pattern(marker) for marker in sorted(self.rules.markers, key=len, reverse=True))
        self._plain = tuple(re.compile(self.rules.patterns[name]) for name in ("email", "phone", "account"))
        self._iban = re.compile(self.rules.patterns["iban"])
        self._extra = tuple(re.compile(item) for item in self.rules.extra_patterns)

    def contains_restricted(self, text: str) -> bool:
        return bool(self._spans(text))

    def mask(self, text: str) -> str:
        spans = self._spans(text)
        if not spans:
            return text
        parts: list[str] = []
        previous = 0
        for start, end in spans:
            parts.append(text[previous:start])
            parts.append(MASK)
            previous = end
        parts.append(text[previous:])
        return "".join(parts)

    def mask_quote(self, quote: str) -> str | None:
        """Return a masked quote, or None when no reviewable words would remain."""
        masked = self.mask(quote)
        if self.contains_restricted(masked):
            return None
        leftover = masked.replace(MASK, "")
        letters = "".join(char for char in leftover if char.isalnum())
        if len(letters) < 3:
            return None
        return masked

    def scrub(self, value: object, key: str | None = None) -> object:
        if isinstance(value, str):
            if key in _ID_KEYS and _HEX_ID.fullmatch(value):
                return value
            if not self.contains_restricted(value):
                return value
            masked = self.mask(value)
            return masked if not self.contains_restricted(masked) else MASK
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            cleaned = {}
            for item_key, item_value in value.items():
                name = item_key if isinstance(item_key, str) else None
                cleaned[item_key] = self.scrub(item_value, name)
            return cleaned
        return value

    def assert_clean(self, text: str) -> None:
        if self.contains_restricted(text):
            raise LeakError("refusing to write a draft that contains a restricted value")

    def _spans(self, text: str) -> list[tuple[int, int]]:
        found: list[tuple[int, int]] = []
        for pattern in (*self._markers, *self._plain, *self._extra):
            found.extend(match.span() for match in pattern.finditer(text))
        for match in self._iban.finditer(text):
            if _iban_ok(match.group()):
                found.append(match.span())
        for match in _CARD.finditer(text):
            digits = re.sub(r"[ -]", "", match.group())
            if not 13 <= len(digits) <= 19 or not digits.isdigit() or not _luhn(digits):
                continue
            if _id_context(text, match.start()):
                continue
            found.append(match.span())
        return _merge(found)
