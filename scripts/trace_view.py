"""Print a case trace as one line per span or event.

    uv run --locked python scripts/trace_view.py reports/traces/CARD-D07.jsonl
"""

import json
import sys
from pathlib import Path

_SKIP = {"run_id"}


def line(record: dict) -> str:
    attrs = " ".join(f"{k}={json.dumps(v)}" for k, v in sorted(record.get("attrs", {}).items()) if k not in _SKIP)
    stamp = record["ts"][11:23]
    if record["kind"] == "span":
        head = f"{stamp} span  {record['name']:<26} {record['duration_ms']:>8.2f} ms {record['status']:<5}"
    else:
        head = f"{stamp} event {record['name']:<26} {'':>11} {record.get('level', 'info'):<5}"
    return f"{head} {attrs}".rstrip()


def main(argv: list[str]) -> int:
    for name in argv:
        path = Path(name)
        records = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        if records:
            first = records[0]
            print(f"# {path.name}  (times UTC)  trace_id={first['trace_id']}  run_id={first['run_id']}  records={len(records)}")
        for record in records:
            print(line(record))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
