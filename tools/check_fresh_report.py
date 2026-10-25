"""Fail when a saved evaluation report differs from a fresh one.

The two arguments are JSON written by tools/evaluate.py. The comparison is
the parsed object, so key order does not matter.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_fresh_report.py SAVED FRESH", file=sys.stderr)
        return 2
    saved = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    fresh = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if saved == fresh:
        passed = fresh.get("passed_cases")
        total = fresh.get("total_cases")
        print(f"evaluation report is current ({passed} of {total})")
        return 0
    print("reports/dev.json does not match a fresh evaluation", file=sys.stderr)
    saved_cases = {item.get("case_id"): item for item in saved.get("cases", []) if isinstance(item, dict)}
    fresh_cases = {item.get("case_id"): item for item in fresh.get("cases", []) if isinstance(item, dict)}
    for case_id in sorted(set(saved_cases) | set(fresh_cases)):
        if saved_cases.get(case_id) != fresh_cases.get(case_id):
            print(f"differs: {case_id}", file=sys.stderr)
    if saved.get("passed") != fresh.get("passed"):
        print(f"passed flag saved={saved.get('passed')} fresh={fresh.get('passed')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
