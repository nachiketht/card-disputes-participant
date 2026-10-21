import json
import threading
from pathlib import Path

from dispute_copilot.serialization import citation_source_text, digest, hash_canonical, idempotency_key
from dispute_copilot.storage import upsert_results, write_draft


def test_hash_and_citation_serializers_stay_distinct():
    value = {"b": 1, "a": "é"}
    assert hash_canonical(value) == '{"a":"é","b":1}'
    assert citation_source_text(value) == '{"a": "\\u00e9", "b": 1}'
    assert digest({"z": 1, "m": "ok"}) == "6a98ed510e6e2e68801c675f21196fd8175dbbda30446ebaa2673c7ec67fd2f3"


def test_idempotency_key_binds_the_review_and_the_hash():
    key = idempotency_key("CARD-D01", "rev_a", "abc")
    assert key == "dc-1929f41e28cbf38319971d51effa98c952206dd3f7824be4a0b3377b2a6347ab"
    assert key != idempotency_key("CARD-D01", "rev_b", "abc")
    assert key != idempotency_key("CARD-D01", "rev_a", "abc", second_review_id="rev_b")


def test_upsert_replaces_the_same_case(tmp_path: Path):
    path = tmp_path / "results.json"
    upsert_results(path, {"case_id": "A", "n": 1})
    upsert_results(path, {"case_id": "B", "n": 1})
    upsert_results(path, {"case_id": "A", "n": 2})
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert {row["case_id"]: row["n"] for row in rows} == {"A": 2, "B": 1}


def test_two_writers_keep_every_case(tmp_path: Path):
    path = tmp_path / "results.json"
    barrier = threading.Barrier(8)

    def worker(number: int) -> None:
        barrier.wait()
        upsert_results(path, {"case_id": f"C{number}", "n": number})

    threads = [threading.Thread(target=worker, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(row["case_id"] for row in rows) == [f"C{number}" for number in range(8)]


def test_each_run_gets_the_next_draft_version(tmp_path: Path):
    write_draft(tmp_path, "CARD-D01", {"case_id": "CARD-D01", "n": 1})
    write_draft(tmp_path, "CARD-D01", {"case_id": "CARD-D01", "n": 2})
    assert (tmp_path / "drafts" / "CARD-D01" / "v1.json").is_file()
    assert (tmp_path / "drafts" / "CARD-D01" / "v2.json").is_file()
    rows = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert rows == [{"case_id": "CARD-D01", "n": 2}]
