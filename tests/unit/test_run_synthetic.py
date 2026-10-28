import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("run_synthetic", ROOT / "tools" / "run_synthetic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_approaches_keeps_order_and_drops_duplicates():
    module = _load()
    assert module.parse_approaches("1,2,3") == ("1", "2", "3")
    assert module.parse_approaches("3, 1, 3") == ("3", "1")


def test_parse_approaches_rejects_unknown_values():
    module = _load()
    with pytest.raises(ValueError, match="ab"):
        module.parse_approaches("1,ab")
    with pytest.raises(ValueError, match="no approaches"):
        module.parse_approaches(" , ")


def test_non_loopback_mock_exits_before_drafting():
    module = _load()
    assert module.main(["--mock-url", "http://example.com", "--no-start-mock"]) == 1


def test_missing_gold_exits_before_drafting(tmp_path):
    module = _load()
    missing = tmp_path / "no_gold.json"
    assert module.main(["--gold", str(missing), "--no-start-mock"]) == 1
