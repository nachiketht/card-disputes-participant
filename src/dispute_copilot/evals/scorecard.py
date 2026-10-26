"""Print one scorecard and save it under reports/.

Dev cases are scored by tools/evaluate.py. Citation support is a separate
check over the markdown corpus. Synthetic cases come from the versioned
sets under evals/sets/. A category below its minimum exits non-zero.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.evals.citations import JoinedCorpus, citation_support
from dispute_copilot.fields import CASE_ID, DEADLINE, NARRATIVE, ROUTING, Routing
from dispute_copilot.llm.guard import rejects
from dispute_copilot.observability.cost import compute_cost, price_call
from dispute_copilot.observability.metrics import compute, read_traces

_ROOT = Path(__file__).resolve().parents[3]
_SETS = _ROOT / "evals" / "sets"
_RULES = _ROOT / "config" / "rules" / "evals.yaml"
_LABELS = (
    ("workflow_correctness", "workflow correctness"),
    ("deadlines", "deadlines"),
    (ROUTING, ROUTING),
    ("grounding", "grounding"),
    ("escalation", "escalation"),
    ("injection_resistance", "injection resistance"),
    ("leak_safety", "pii and leak safety"),
    ("no_commitment", "no-commitment language"),
    ("approval_gating", "idempotency and approval gating"),
    ("latency", "latency"),
    ("cost", "cost"),
)


def write_scorecard(
    *,
    submission: Path,
    data_dir: Path,
    report_path: Path,
    trace_dir: Path,
    pytest_rows: list[dict] | None = None,
) -> tuple[int, str]:
    card = build_scorecard(
        submission=submission,
        data_dir=data_dir,
        trace_dir=trace_dir,
        pytest_rows=pytest_rows,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return (0 if card["passed"] else 1), render(card)


def build_scorecard(
    *,
    submission: Path,
    data_dir: Path,
    trace_dir: Path,
    pytest_rows: list[dict] | None = None,
) -> dict:
    rules = _rules()
    sets = _sets()
    gold_path = _ROOT / "feedback" / "dev_gold.json"
    official = _official(data_dir, gold_path, submission)
    drafts = _drafts(submission)
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    by_id = {item.get(CASE_ID): item for item in drafts if isinstance(item, dict)}
    support = citation_support(JoinedCorpus(FileCorpus(data_dir), _payload_sources(data_dir)), drafts)
    nodes = _nodes(sets)
    if pytest_rows is None and nodes:
        pytest_rows = _pytest([item["node"] for item in nodes.values()])
    pytest_rows = pytest_rows or []
    found = {row["node"]: bool(row.get("passed")) for row in pytest_rows}
    categories = []
    for key, label in _LABELS:
        if key == "cost":
            categories.append(_cost_category(trace_dir, rules, label))
            continue
        if key == "latency":
            categories.append(_latency_category(trace_dir, rules, label))
            continue
        extra = _extra_rate(nodes, found, key)
        if key == "grounding":
            score = _ratio(support["supported"], support["total"])
            detail = f'{support["supported"]}/{support["total"]}. {support["limit"]}'
        elif key == "no_commitment":
            score, detail = _commitment(gold, by_id)
        elif key == "escalation":
            score, detail = _escalation(gold, by_id)
            score = _min_rate(score, extra)
            if extra is not None:
                detail = f"{detail}; synthetic {extra}"
        elif key == "workflow_correctness":
            score = _ratio(official.get("passed_cases"), official.get("total_cases"))
            detail = f'{official.get("passed_cases")}/{official.get("total_cases")}'
            score = _min_rate(score, extra)
        elif key == "deadlines":
            score = _check_rate(official, DEADLINE)
            detail = _check_detail(official, DEADLINE)
            score = _min_rate(score, extra)
        elif key == ROUTING:
            score = _check_rate(official, ROUTING)
            detail = _check_detail(official, ROUTING)
            score = _min_rate(score, extra)
        elif key == "leak_safety":
            score = _check_rate(official, "no_restricted_values")
            detail = _check_detail(official, "no_restricted_values")
            score = _min_rate(score, extra)
        else:
            score = extra
            detail = "synthetic cases" if extra is not None else "no cases"
        minimum = rules["minimum"].get(key)
        categories.append(_row(key, label, score, minimum, detail))
    passed = all(item["passed"] for item in categories)
    return {
        "passed": passed,
        "categories": categories,
        "citation_support": support,
        "sets": _set_rows(sets, official, found),
        "official_passed_cases": official.get("passed_cases"),
        "official_total_cases": official.get("total_cases"),
    }


def render(card: dict) -> str:
    lines = ["scorecard"]
    for item in card["categories"]:
        score = "unmeasured" if item["score"] is None else f'{item["score"]:.4f}'
        minimum = "none" if item["minimum"] is None else f'{item["minimum"]:.4f}'
        flag = "pass" if item["passed"] else "fail"
        lines.append(f'{item["label"]}: {score}  minimum {minimum}  {flag}  {item["detail"]}')
    lines.append(
        "citation support: "
        f'{card["citation_support"]["supported"]}/{card["citation_support"]["total"]}. '
        f'{card["citation_support"]["limit"]}'
    )
    for item in card["sets"]:
        lines.append(f'set {item["id"]} v{item["version"]}: {item["passed_cases"]}/{item["total_cases"]}')
    lines.append("passed" if card["passed"] else "failed")
    return "\n".join(lines)


def _payload_sources(data_dir: Path) -> dict[str, str]:
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "tools" / "source_index.py"), "--data", str(data_dir)],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return {}
    try:
        loaded = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    sources = loaded.get("sources") if isinstance(loaded, dict) else None
    if not isinstance(sources, dict):
        return {}
    return {key: value for key, value in sources.items() if isinstance(key, str) and isinstance(value, str)}


def _rules() -> dict:
    loaded = yaml.safe_load(_RULES.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("minimum"), dict):
        raise ValueError("evals.yaml needs a minimum map")
    return loaded


def _sets() -> list[dict]:
    found = []
    for path in sorted(_SETS.glob("*/v1.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            found.append(loaded)
    return found


def _nodes(sets: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for item in sets:
        for node in item.get("nodes") or []:
            if isinstance(node, dict) and isinstance(node.get("node"), str):
                found[node["node"]] = node
    return found


def _official(data_dir: Path, gold: Path, submission: Path) -> dict:
    if not submission.is_file():
        return {"passed": False, "passed_cases": 0, "total_cases": 0, "cases": [], "format_errors": ["missing submission"]}
    proc = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "tools" / "evaluate.py"),
            "--data",
            str(data_dir),
            "--gold",
            str(gold),
            "--submission",
            str(submission),
        ],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    try:
        loaded = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"passed": False, "passed_cases": 0, "total_cases": 0, "cases": [], "format_errors": ["evaluator output was not json"]}
    return loaded if isinstance(loaded, dict) else {"passed": False, "passed_cases": 0, "total_cases": 0, "cases": []}


def _drafts(submission: Path) -> list[dict]:
    if not submission.is_file():
        return []
    try:
        loaded = json.loads(submission.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _pytest(nodes: list[str]) -> list[dict]:
    with tempfile.TemporaryDirectory() as folder:
        dest = Path(folder) / "rows.json"
        env = os.environ.copy()
        env["EVAL_RECORD"] = str(dest)
        env["LLM_PROVIDER"] = "null"
        env["FEATURE_LLM_REVIEW"] = "off"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=line", *nodes],
            cwd=_ROOT,
            env=env,
            text=True,
        )
        if not dest.is_file():
            return []
        loaded = json.loads(dest.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, list) else []


def _extra_rate(nodes: dict[str, dict], found: dict[str, bool], category: str) -> float | None:
    selected = [node for node, item in nodes.items() if item.get("category") == category]
    if not selected:
        return None
    ok = sum(1 for node in selected if found.get(node) is True)
    return _ratio(ok, len(selected))


def _check_rate(official: dict, name: str) -> float | None:
    cases = official.get("cases")
    if not isinstance(cases, list) or not cases:
        return None
    ok = 0
    for item in cases:
        checks = item.get("checks") if isinstance(item, dict) else None
        if isinstance(checks, dict) and checks.get(name) is True:
            ok += 1
    return _ratio(ok, len(cases))


def _check_detail(official: dict, name: str) -> str:
    cases = official.get("cases") if isinstance(official.get("cases"), list) else []
    ok = 0
    for item in cases:
        checks = item.get("checks") if isinstance(item, dict) else None
        if isinstance(checks, dict) and checks.get(name) is True:
            ok += 1
    return f"{ok}/{len(cases)}"


def _commitment(gold: dict, by_id: dict) -> tuple[float | None, str]:
    if not gold:
        return None, "no cases"
    ok = 0
    for case_id in gold:
        item = by_id.get(case_id)
        narrative = item.get(NARRATIVE) if isinstance(item, dict) else None
        if isinstance(narrative, str) and narrative.strip() and not rejects(narrative):
            ok += 1
    return _ratio(ok, len(gold)), f"{ok}/{len(gold)}"


def _escalation(gold: dict, by_id: dict) -> tuple[float | None, str]:
    targets = []
    for case_id, answer in gold.items():
        allowed = answer.get(ROUTING) if isinstance(answer, dict) else None
        if not isinstance(allowed, list):
            continue
        if Routing.MANUAL_REVIEW.value in allowed or Routing.REQUEST_INFORMATION.value in allowed:
            targets.append(case_id)
    if not targets:
        return None, "no escalation cases"
    ok = 0
    for case_id in targets:
        item = by_id.get(case_id)
        routing = item.get(ROUTING) if isinstance(item, dict) else None
        if routing in gold[case_id][ROUTING]:
            ok += 1
    return _ratio(ok, len(targets)), f"{ok}/{len(targets)}"


def _latency_category(trace_dir: Path, rules: dict, label: str) -> dict:
    records = read_traces(trace_dir) if trace_dir.is_dir() else []
    score = compute(records).get("good_case_rate") if records else None
    detail = "no case spans" if score is None else f"good-case rate {score}"
    return _row("latency", label, score, rules["minimum"].get("latency"), detail)


def _cost_category(trace_dir: Path, rules: dict, label: str) -> dict:
    records = read_traces(trace_dir) if trace_dir.is_dir() else []
    calls = [item for item in records if item.get("kind") == "span" and item.get("name") == "model.call"]
    priced = []
    for item in calls:
        attrs = item.get("attrs") if isinstance(item.get("attrs"), dict) else {}
        model = str(attrs.get("model") or "")
        tokens_in = attrs.get("tokens_in")
        tokens_out = attrs.get("tokens_out")
        if isinstance(tokens_in, int) and isinstance(tokens_out, int):
            priced.append(price_call(model, tokens_in, tokens_out))
    cap = (rules.get("cost") or {}).get("maximum_usd")
    if not calls:
        detail = "no model calls; " + compute_cost()["status"]
        return _row("cost", label, None, None if cap is None else float(cap), detail, passed=True)
    if any(item["status"] != "estimate from list price" for item in priced) or len(priced) != len(calls):
        named = [item["status"] for item in priced if item["status"] != "estimate from list price"]
        detail = named[0] if named else "unmeasured"
        return _row("cost", label, None, None if cap is None else float(cap), detail, passed=cap is None)
    total = round(sum(float(item["usd"] or 0) for item in priced), 6)
    passed = True if cap is None else total <= float(cap)
    return _row(
        "cost",
        label,
        total,
        None if cap is None else float(cap),
        f"estimate from list price {total}",
        passed=passed,
    )


def _set_rows(sets: list[dict], official: dict, found: dict[str, bool]) -> list[dict]:
    rows = []
    for item in sets:
        if item.get("kind") == "official":
            rows.append(
                {
                    "id": item.get("id"),
                    "version": item.get("version"),
                    "passed_cases": official.get("passed_cases"),
                    "total_cases": official.get("total_cases"),
                }
            )
            continue
        nodes = [node.get("node") for node in item.get("nodes") or [] if isinstance(node, dict)]
        ok = sum(1 for node in nodes if found.get(node) is True)
        rows.append({"id": item.get("id"), "version": item.get("version"), "passed_cases": ok, "total_cases": len(nodes)})
    return rows


def _row(key: str, label: str, score: float | None, minimum: float | None, detail: str, passed: bool | None = None) -> dict:
    if passed is None:
        passed = score is not None and minimum is not None and score >= minimum
        if minimum is None:
            passed = True
    return {"id": key, "label": label, "score": score, "minimum": minimum, "passed": passed, "detail": detail}


def _min_rate(left: float | None, right: float | None) -> float | None:
    rates = [item for item in (left, right) if item is not None]
    if not rates:
        return None
    return min(rates)


def _ratio(part, whole) -> float | None:
    if not isinstance(part, int) or not isinstance(whole, int) or whole == 0:
        return None
    return round(part / whole, 4)
