"""Run metrics from trace records, and alert evaluation against config/rules/slo.yaml.

The SLO measure is the good-case rate: a case is good when its case span has
outcome drafted and duration_ms at or under the latency target. The route is
not read. The other metrics explain a miss: per-case latency, tool exhaustion,
retries, breaker opens and flagged untrusted content.
"""

import json
import operator
import statistics
from pathlib import Path

import yaml

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID, Routing

_ROOT = Path(__file__).resolve().parents[3]
SLO_FILE = _ROOT / "config" / "rules" / "slo.yaml"
_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def load_slo(path: Path | None = None) -> dict:
    loaded = yaml.safe_load((path or SLO_FILE).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "slo" not in loaded or "alerts" not in loaded:
        raise ValueError("slo.yaml needs slo and alerts")
    return loaded


def read_traces(trace_dir: Path) -> list[dict]:
    records: list[dict] = []
    if not trace_dir.is_dir():
        return records
    for path in sorted(trace_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(-(-fraction * len(ordered) // 1)))
    return ordered[min(rank, len(ordered)) - 1]


def compute(records: list[dict], slo: dict | None = None) -> dict:
    slo = slo or load_slo()
    target_ms = float(slo["slo"]["latency_target_ms"])
    roots = [r for r in records if r.get("kind") == "span" and r.get("name") == "case"]
    latencies = [float(r["duration_ms"]) for r in roots]
    drafted = [r for r in roots if r.get("attrs", {}).get("outcome") == "drafted"]
    good = [r for r in drafted if float(r["duration_ms"]) <= target_ms]
    calls = [r for r in records if r.get("kind") == "span" and r.get("name") == "tool.call"]
    exhausted = [r for r in calls if r.get("attrs", {}).get("outcome") == "exhausted"]
    events = [r for r in records if r.get("kind") == "event"]
    skips = [r for r in events if r["name"] == "breaker.skip"]
    opens = [r for r in events if r["name"] == "breaker.transition" and r["attrs"].get("to_state") == "open"]
    flagged = {r[CASE_ID] for r in events if r["name"] == "security.untrusted_instruction"}
    injected = {r[CASE_ID] for r in events if r["name"] == "injection_detected"}
    blocked = [r for r in events if r["name"] == "review.gate" and r["attrs"].get("passed") is False]
    routed = {}
    for r in drafted:
        route = r.get("attrs", {}).get("route")
        routed[route] = routed.get(route, 0) + 1
    per_dependency = {}
    for r in calls:
        name = r["attrs"].get("dependency")
        row = per_dependency.setdefault(name, {"calls": 0, "exhausted": 0, "attempts": 0})
        row["calls"] += 1
        row["attempts"] += int(r["attrs"].get("attempts") or 0)
        row["exhausted"] += 1 if r["attrs"].get("outcome") == "exhausted" else 0
    total = len(roots)
    metrics = {
        "cases_total": total,
        "cases_drafted": len(drafted),
        "valid_contract_rate": _ratio(len(drafted), total),
        "good_case_rate": _ratio(len(good), total),
        "case_latency_ms": {
            "n": len(latencies),
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "tool_calls": len(calls) + len(skips),
        "tool_exhausted": len(exhausted),
        "tool_exhausted_rate": _ratio(len(exhausted), len(calls)),
        "tool_retries": sum(1 for r in events if r["name"] == "retry.backoff"),
        "tool_by_dependency": per_dependency,
        "breaker_opens": len(opens),
        "breaker_skips": len(skips),
        "untrusted_instruction_cases": len(flagged),
        "injection_detected": len(injected),
        "gate_blocked": len(blocked),
        "routes": routed,
        "manual_review_rate": _ratio(routed.get(Routing.MANUAL_REVIEW.value, 0), len(drafted)),
    }
    budget_cases = (1 - float(slo["slo"]["objective"])) * total
    bad = total - len(good)
    metrics["error_budget"] = {
        "objective": slo["slo"]["objective"],
        "allowed_bad_cases": round(budget_cases, 3),
        "bad_cases": bad,
        "remaining_fraction": None if budget_cases == 0 else round(1 - bad / budget_cases, 3),
    }
    return metrics


def evaluate_alerts(metrics: dict, slo: dict | None = None) -> list[dict]:
    slo = slo or load_slo()
    fired = []
    for rule in slo["alerts"]:
        value = metrics.get(rule["metric"])
        if value is None:
            continue
        if _OPS[rule["op"]](value, rule["threshold"]):
            fired.append({"alert": rule["name"], "severity": rule["severity"], "metric": rule["metric"], "value": value,
                          "threshold": f'{rule["op"]} {rule["threshold"]}'})
    return fired


def write_report(path: Path, records: list[dict], extra: dict | None = None) -> dict:
    slo = load_slo()
    metrics = compute(records, slo)
    report = {
        "slo": slo["slo"],
        "metrics": metrics,
        "alerts_fired": evaluate_alerts(metrics, slo),
        "alert_rules": slo["alerts"],
        **(extra or {}),
    }
    report = Redactor().scrub(report)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    redactor = Redactor()
    if redactor.contains_restricted(rendered):
        masked = redactor.mask(rendered)
        rendered = masked if not redactor.contains_restricted(masked) else json.dumps({"redacted": True}) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return report


def _ratio(part: int, whole: int) -> float | None:
    if whole == 0:
        return None
    return round(part / whole, 4)
