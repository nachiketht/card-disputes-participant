import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gates():
    spec = importlib.util.spec_from_file_location("lint_gates", ROOT / "tools" / "lint_gates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_tree_passes_gates():
    assert _gates().scan_tree() == []


def test_scanner_sees_a_contract_literal():
    found = [value for _, value in _gates().string_literals('name = "case_id"\n')]
    assert "case_id" in found
