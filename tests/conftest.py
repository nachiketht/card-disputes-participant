"""Optional recorder for the scorecard. Quiet unless EVAL_RECORD is set."""

import json
import os
from pathlib import Path


def pytest_runtest_logreport(report) -> None:
    dest = os.environ.get("EVAL_RECORD")
    if not dest:
        return
    if report.when != "call" and not (report.when == "setup" and report.failed):
        return
    path = Path(dest)
    rows = []
    if path.is_file() and path.stat().st_size:
        rows = json.loads(path.read_text(encoding="utf-8"))
    rows.append({"node": report.nodeid, "passed": bool(report.passed)})
    path.write_text(json.dumps(rows), encoding="utf-8")
