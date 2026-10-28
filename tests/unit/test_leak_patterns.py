"""Pattern leak checks: real restricted values are masked, identifiers are not."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dispute_copilot.errors import LeakError
from dispute_copilot.evidence.redaction import MASK, Redactor, load_redaction
from dispute_copilot.observability.metrics import write_report
from dispute_copilot.observability.tracing import case_trace

ROOT = Path(__file__).resolve().parents[2]
PAN = "4111111111111111"
PAN_SPACED = "4111 1111 1111 1111"
PAN_DASHED = "4111-1111-1111-1111"
IBAN = "GB82WEST12345698765432"
EMAIL = "buyer@example.com"
PHONE = "202-555-0147"
PHONE_INTL = "+44 20 7946 0958"
ACCOUNT = "Account 1234567890"
SPAN_ID = "abcdef0123456789"
TRACE_ID = "0123456789abcdef0123456789abcdef"


def _runtime():
    spec = importlib.util.spec_from_file_location("runtime_under_test", ROOT / "tools" / "runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configured_rules_cover_markers_cards_and_contact_details():
    redactor = Redactor()
    rules = load_redaction()
    assert "markers" in yaml.safe_load((ROOT / "config" / "rules" / "redaction.yaml").read_text())["enabled"]
    assert any(marker.startswith("SYNTHETIC-") for marker in rules.markers)
    assert redactor.contains_restricted("SYNTHETIC-" + "PRIVATE-CARD-D01")
    assert redactor.contains_restricted("id " + "CUST" + "-D01 tail")
    assert redactor.contains_restricted("private_" + "token")
    assert redactor.contains_restricted(PAN)
    assert redactor.contains_restricted(PAN_SPACED)
    assert redactor.contains_restricted(PAN_DASHED)
    assert redactor.contains_restricted(IBAN)
    assert redactor.contains_restricted(EMAIL)
    assert redactor.contains_restricted(PHONE)
    assert redactor.contains_restricted(PHONE_INTL)
    assert redactor.contains_restricted("+442071234567")
    assert redactor.contains_restricted(ACCOUNT)
    assert PAN not in redactor.mask(f"card {PAN_DASHED} on file")
    assert EMAIL not in redactor.mask(f"write {EMAIL} please")
    assert IBAN not in redactor.mask(f"iban {IBAN}")
    assert "1234567890" not in redactor.mask(ACCOUNT)


def test_span_ids_dates_and_ordinary_words_are_not_restricted():
    redactor = Redactor()
    samples = [
        SPAN_ID,
        TRACE_ID,
        json.dumps({"span_id": SPAN_ID, "trace_id": TRACE_ID, "parent_id": SPAN_ID}),
        json.dumps({"span_id": PAN}),
        "2026-10-01",
        "amount 117",
        "CARD-POLICY-V2",
        "TX-D01",
        "Morrow Supplies",
        "user@localhost",
        "Customer account numbers stay out of the summary.",
        "1234567890123456",
    ]
    for sample in samples:
        assert not redactor.contains_restricted(sample), sample
    assert redactor.mask(SPAN_ID) == SPAN_ID
    assert redactor.scrub({"span_id": PAN, "note": PAN}) == {"span_id": PAN, "note": MASK}


def test_redaction_config_rejects_broken_files(tmp_path):
    missing = tmp_path / "missing.yaml"
    rules = load_redaction(missing)
    assert "email" in rules.patterns
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("[1]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_redaction(mapping)
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        "enabled: [markers, card, iban, account, email, phone]\ncard: luhn\nnope: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        load_redaction(unknown)
    enabled = tmp_path / "enabled.yaml"
    enabled.write_text("enabled: [markers]\ncard: luhn\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must enable"):
        load_redaction(enabled)
    card = tmp_path / "card.yaml"
    card.write_text(
        "enabled: [markers, card, iban, account, email, phone]\ncard: none\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must stay luhn"):
        load_redaction(card)
    markers = tmp_path / "markers.yaml"
    markers.write_text(
        "enabled: [markers, card, iban, account, email, phone]\ncard: luhn\nmarkers: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="markers must be lists"):
        load_redaction(markers)
    patterns = tmp_path / "patterns.yaml"
    patterns.write_text(
        "enabled: [markers, card, iban, account, email, phone]\ncard: luhn\npatterns: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="patterns must be a mapping"):
        load_redaction(patterns)
    unknown_patterns = tmp_path / "unknown_patterns.yaml"
    unknown_patterns.write_text(
        "\n".join(
            [
                "enabled: [markers, card, iban, account, email, phone]",
                "card: luhn",
                "patterns:",
                "  nope: x",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown patterns"):
        load_redaction(unknown_patterns)
    empty_pattern = tmp_path / "empty_pattern.yaml"
    empty_pattern.write_text(
        "\n".join(
            [
                "enabled: [markers, card, iban, account, email, phone]",
                "card: luhn",
                "patterns:",
                "  email: '   '",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="regular expression"):
        load_redaction(empty_pattern)
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        "\n".join(
            [
                "enabled: [markers, card, iban, account, email, phone]",
                "card: luhn",
                "extra_patterns: [1]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extra_patterns"):
        load_redaction(extra)
    with pytest.raises(LeakError):
        Redactor().assert_clean(PAN)
    assert Redactor().mask_quote("ab") is None
    assert Redactor().mask_quote(PAN) is None
    from dispute_copilot.evidence.redaction import _iban_ok, _merge

    assert _iban_ok("GB12") is False
    assert _iban_ok("GB12WEST1234-987654") is False
    assert _merge([]) == []
    assert _merge([(0, 4), (2, 8), (10, 12)]) == [(0, 8), (10, 12)]
    redactor = Redactor()
    redactor.mask = lambda quote: quote
    redactor.contains_restricted = lambda text: True
    assert redactor.mask_quote("still dirty") is None


def test_extra_patterns_extend_the_config(tmp_path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "\n".join(
            [
                "enabled: [markers, card, iban, account, email, phone]",
                "markers: []",
                "card: luhn",
                "patterns: {}",
                "extra_markers: [CASE-SECRET]",
                "extra_patterns: ['SECRET-CODE-[0-9]+']",
                "",
            ]
        ),
        encoding="utf-8",
    )
    redactor = Redactor(path)
    assert redactor.contains_restricted("CASE-SECRET-7")
    assert redactor.contains_restricted("see SECRET-CODE-42")
    assert "SECRET-CODE-42" not in redactor.mask("see SECRET-CODE-42")
    assert redactor.contains_restricted(PAN)


def test_a_trace_keeps_span_ids_and_masks_a_card(tmp_path):
    with case_trace("CARD-T9", "run-1", tmp_path) as tracer:
        with tracer.span("case", note=PAN) as span:
            span_id = span.span_id
            tracer.event("note.recorded", detail=EMAIL)
    text = (tmp_path / "CARD-T9.jsonl").read_text(encoding="utf-8")
    assert span_id in text
    assert PAN not in text
    assert EMAIL not in text
    assert MASK in text


def test_a_saved_report_masks_contact_details(tmp_path):
    path = tmp_path / "metrics.json"
    write_report(path, [], {"note": f"reply to {EMAIL}"})
    text = path.read_text(encoding="utf-8")
    assert EMAIL not in text
    assert MASK in text


def test_audit_output_masks_a_card(tmp_path):
    runtime = _runtime()
    state = tmp_path / "mock.sqlite"
    db = runtime.connect(state)
    db.execute(
        "INSERT INTO audit(at, event, case_id, detail) VALUES(?, ?, ?, ?)",
        ("t", "note", "CARD-D01", json.dumps({"note": PAN})),
    )
    db.commit()
    db.close()
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "runtime.py"), "audit", "--state", str(state)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert PAN not in result.stdout
    assert MASK in result.stdout
    parsed = json.loads(result.stdout)
    assert parsed[0]["case_id"] == "CARD-D01"
