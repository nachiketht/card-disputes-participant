from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.fields import Classification
from dispute_copilot.retrieval.policy import PolicyResolver
from dispute_copilot.rules.deadline import add_calendar_days

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dev"


def test_policy_boundaries_follow_the_effective_dates():
    resolver = PolicyResolver(FileCorpus(DATA))
    august = resolver.select(date(2026, 8, 31))
    september = resolver.select(date(2026, 9, 1))
    assert august.policy_id == "CARD-POLICY-V1"
    assert august.days[Classification.UNAUTHORIZED] == 20
    assert september.policy_id == "CARD-POLICY-V2"
    assert september.days[Classification.UNAUTHORIZED] == 30
    assert resolver.select(date(2026, 1, 1)).policy_id == "CARD-POLICY-V1"
    assert resolver.select(date(2026, 12, 31)).policy_id == "CARD-POLICY-V2"
    assert resolver.select(date(2027, 1, 15)).in_range is False
    assert resolver.select(date(2025, 12, 31)).policy_id is None


def test_a_new_policy_file_is_selected_by_its_dates(tmp_path):
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "CARD-PROCEDURE-01.md").write_text(
        "Source ID: CARD-PROCEDURE-01\nRouting precedence: manual_review for unsupported/ambiguous cases;\n",
        encoding="utf-8",
    )
    (policies / "CARD-POLICY-NEXT.md").write_text(
        "\n".join(
            [
                "Source ID: CARD-POLICY-NEXT",
                "Effective from: 2027-01-01",
                "Effective through: 2027-12-31",
                "Unauthorized deadline days: 10",
                "Duplicate deadline days: 11",
                "Service deadline days: 12",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "dataset.json").write_text('{"leak":"SENTINEL-DATASET"}', encoding="utf-8")
    corpus = FileCorpus(tmp_path)
    assert all("SENTINEL-DATASET" not in corpus.get(source_id) for source_id in corpus.ids())
    selection = PolicyResolver(corpus).select(date(2027, 6, 1))
    assert selection.policy_id == "CARD-POLICY-NEXT"
    assert selection.days[Classification.UNAUTHORIZED] == 10


def test_overlapping_policies_are_an_error():
    text = "\n".join(
        [
            "Effective from: 2026-01-01",
            "Effective through: 2026-12-31",
            "Unauthorized deadline days: 1",
            "Duplicate deadline days: 1",
            "Service deadline days: 1",
        ]
    )
    from dispute_copilot.adapters.corpus import MemoryCorpus

    corpus = MemoryCorpus(
        {
            "CARD-PROCEDURE-01": "Source ID: CARD-PROCEDURE-01\n",
            "CARD-POLICY-A": "Source ID: CARD-POLICY-A\n" + text,
            "CARD-POLICY-B": "Source ID: CARD-POLICY-B\n" + text,
        }
    )
    with pytest.raises(ValueError, match="overlapping"):
        PolicyResolver(corpus).select(date(2026, 6, 1))


@given(
    reported=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
    days=st.integers(min_value=0, max_value=4000),
)
@settings(max_examples=40)
def test_deadline_arithmetic(reported, days):
    assert add_calendar_days(reported, days) == reported + timedelta(days=days)


def test_deadline_rejects_a_negative_count():
    with pytest.raises(ValueError):
        add_calendar_days(date(2026, 10, 1), -1)


def test_rules_do_not_read_the_wall_clock():
    root = ROOT / "src" / "dispute_copilot"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "datetime.now" not in text
        assert "date.today" not in text


def test_known_dev_deadline_dates():
    assert add_calendar_days(date(2026, 10, 1), 30) == date(2026, 10, 31)
    assert add_calendar_days(date(2026, 8, 31), 20) == date(2026, 9, 20)
