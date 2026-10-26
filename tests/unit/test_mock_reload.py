import importlib.util
import json
import os
import sqlite3
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    spec = importlib.util.spec_from_file_location("runtime", ROOT / "tools" / "runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "intake_source": f"{case_id}-INTAKE",
        "documents": [f"{case_id}-INTAKE"],
        "logical_key": case_id.lower(),
        "tools": {
            "transactions": {"source_id": "T", "transactions": []},
            "merchant": {"source_id": "M", "display_name": "M"},
        },
        "faults": {},
    }


def _write(path: Path, cases: list[dict], *, name: str = "fixture") -> None:
    path.write_text(
        json.dumps({"name": name, "allow_write": True, "cases": cases}),
        encoding="utf-8",
    )
    stat = path.stat()
    stamp = stat.st_mtime_ns + 1_000_000
    os.utime(path, ns=(stamp, stamp))


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
        return json.loads(response.read().decode())


def test_a_case_appended_after_startup_is_served_and_old_counts_stay(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    fixture = data / "dataset.json"
    _write(fixture, [_case("CARD-OLD")])
    state = tmp_path / "run.sqlite"
    runtime = _runtime()
    server = runtime.create_server(data, state, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        _get(port, "/v1/cases/CARD-OLD/transactions")
        _write(fixture, [_case("CARD-OLD"), _case("CARD-NEW")], name="fixture-next")
        listed = _get(port, "/v1/cases")
        assert [row["case_id"] for row in listed["cases"]] == ["CARD-OLD", "CARD-NEW"]
        assert _get(port, "/health")["dataset"] == "fixture-next"
        with sqlite3.connect(state) as db:
            count = db.execute(
                "SELECT n FROM calls WHERE case_id=? AND tool=?",
                ("CARD-OLD", "transactions"),
            ).fetchone()
        assert count[0] == 1
        fixture.write_text("{", encoding="utf-8")
        stat = fixture.stat()
        os.utime(fixture, ns=(stat.st_mtime_ns + 1_000_000, stat.st_mtime_ns + 1_000_000))
        listed_again = _get(port, "/v1/cases")
        assert [row["case_id"] for row in listed_again["cases"]] == ["CARD-OLD", "CARD-NEW"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
