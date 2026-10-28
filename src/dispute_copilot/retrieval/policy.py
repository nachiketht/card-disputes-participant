"""Pick the policy version whose effective dates contain the reported date.

Transaction date and the wall clock are not inputs. Two policies that both
contain the date are an error, not a tie to break quietly. A date outside
every version is reported as out of range; the caller decides the branch.
"""

from dataclasses import dataclass
from datetime import date

from dispute_copilot.adapters.cases_blob import BlobCorpus
from dispute_copilot.adapters.corpus import FileCorpus, MemoryCorpus
from dispute_copilot.fields import Classification
from dispute_copilot.rules.documents import labeled_lines

PROCEDURE_ID = "CARD-PROCEDURE-01"
_DATE_LABELS = frozenset({"Effective from", "Effective through"})
_DAY_LABEL = {
    Classification.UNAUTHORIZED: "Unauthorized deadline days",
    Classification.DUPLICATE_CHARGE: "Duplicate deadline days",
    Classification.SERVICE_NOT_RECEIVED: "Service deadline days",
}


@dataclass(frozen=True)
class PolicyVersion:
    policy_id: str
    text: str
    start: date
    end: date
    days: dict[Classification, int]


@dataclass(frozen=True)
class PolicySelection:
    in_range: bool
    policy_id: str | None
    policy_text: str | None
    days: dict[Classification, int]
    procedure_id: str
    procedure_text: str


class PolicyResolver:
    def __init__(self, corpus: FileCorpus | MemoryCorpus | BlobCorpus) -> None:
        self.procedure_text = corpus.get(PROCEDURE_ID)
        self.procedure_id = PROCEDURE_ID
        self._versions: list[PolicyVersion] = []
        for source_id in corpus.ids():
            if not source_id.startswith("CARD-POLICY-"):
                continue
            text = corpus.get(source_id)
            dates = labeled_lines(text, _DATE_LABELS)
            start = date.fromisoformat(dates["Effective from"])
            end = date.fromisoformat(dates["Effective through"])
            if end < start:
                raise ValueError(f"{source_id} ends before it starts")
            days = {}
            for classification, label in _DAY_LABEL.items():
                days[classification] = _day_count(text, label, source_id)
            self._versions.append(PolicyVersion(source_id, text, start, end, days))
        if not self._versions:
            raise ValueError("no policy versions were loaded")

    def select(self, reported: date) -> PolicySelection:
        matches = [item for item in self._versions if item.start <= reported <= item.end]
        if len(matches) > 1:
            names = ", ".join(item.policy_id for item in matches)
            raise ValueError(f"overlapping policies for {reported.isoformat()}: {names}")
        if not matches:
            return PolicySelection(False, None, None, {}, self.procedure_id, self.procedure_text)
        chosen = matches[0]
        return PolicySelection(
            True,
            chosen.policy_id,
            chosen.text,
            dict(chosen.days),
            self.procedure_id,
            self.procedure_text,
        )

    def covering_id(self, day: date) -> str | None:
        return self.select(day).policy_id


def deadline_line(policy_text: str, classification: Classification) -> str:
    label = _DAY_LABEL[classification]
    prefix = f"{label}: "
    for line in policy_text.splitlines():
        if line.startswith(prefix):
            return line
    raise ValueError(f"policy has no {label} line")


def _day_count(text: str, label: str, source_id: str) -> int:
    prefix = f"{label}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            try:
                count = int(line[len(prefix):].strip())
            except ValueError as exc:
                raise ValueError(f"{source_id} has a non-numeric {label}") from exc
            if count < 0:
                raise ValueError(f"{source_id} has a negative {label}")
            return count
    raise ValueError(f"{source_id} has no {label} line")
