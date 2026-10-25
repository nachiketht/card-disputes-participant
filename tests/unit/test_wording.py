import copy
from pathlib import Path

import pytest
import yaml

from dispute_copilot.rules.wording import load_wording, parse_wording, procedure_stub, validate_openings

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "config" / "rules" / "decision_table.yaml"


def _table() -> dict:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def test_a_duplicate_row_id_is_rejected():
    data = _table()
    data["phrases"].append({"id": "recognition", "values": ["again"]})
    with pytest.raises(ValueError, match="duplicate row id recognition"):
        parse_wording(data)


def test_an_unknown_type_id_is_rejected():
    data = _table()
    data["openings"].append({"id": "not-a-type", "prefix": "Nope"})
    with pytest.raises(ValueError, match="unknown type id not-a-type"):
        parse_wording(data)


def test_an_unknown_missing_field_id_is_rejected():
    data = _table()
    data["missing_openings"].append({"id": "not-a-field", "prefix": "Nope"})
    with pytest.raises(ValueError, match="unknown missing-field id not-a-field"):
        parse_wording(data)


def test_an_opening_missing_from_the_procedure_is_rejected():
    with pytest.raises(ValueError, match="opening matches 0 lines"):
        validate_openings(load_wording(), "Source ID: CARD-PROCEDURE-01\n")


def test_an_opening_that_starts_two_lines_is_rejected():
    text = procedure_stub("CARD-PROCEDURE-01") + "Routing precedence: again\n"
    with pytest.raises(ValueError, match="routing_line opening matches 2 lines"):
        validate_openings(load_wording(), text)
