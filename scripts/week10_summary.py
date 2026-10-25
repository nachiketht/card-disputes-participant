"""Short summaries for the Week 10 evidence script.

    dispute-copilot run-all | python scripts/week10_summary.py run
    python scripts/week10_summary.py eval reports/dev.json
    python tools/runtime.py audit --state S | python scripts/week10_summary.py audit
"""

import collections
import json
import sys


def run() -> None:
    data = json.load(sys.stdin)
    for item in data["written"]:
        facts = item["facts"]
        print(f"  {item['case_id']}: {item['classification']:<22} {item['routing']:<20} amount={facts['amount']}")
    print(f"  written={len(data['written'])} not_written={data['not_written']}")
    print(f"  run_id={data.get('run_id')} alerts_fired={json.dumps(data.get('alerts_fired'))}")


def evaluation(path: str) -> None:
    report = json.load(open(path, encoding="utf-8"))
    cases = report.get("cases", [])
    for case in cases:
        failed = [name for name, ok in case.get("checks", {}).items() if not ok]
        print(f"  {case['case_id']}: {'PASS' if case['passed'] else 'FAIL ' + ','.join(failed)}")
    passed = sum(1 for case in cases if case["passed"])
    print(f"  passed_cases={passed}/{len(cases)} overall_passed={report.get('passed')}")


def audit() -> None:
    attempts = collections.OrderedDict()
    for row in json.load(sys.stdin):
        if row["event"] == "tool_read":
            detail = json.loads(row["detail"])
            attempts.setdefault((row["case_id"], detail["tool"]), []).append(detail["status"])
    for (case, tool), statuses in attempts.items():
        if len(statuses) > 1 or 503 in statuses:
            print(f"  {case} {tool}: attempts={len(statuses)} statuses={statuses}")


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "run":
        run()
    elif command == "eval":
        evaluation(sys.argv[2])
    else:
        audit()
