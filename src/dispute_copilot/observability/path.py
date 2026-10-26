"""Request path drawn from saved spans and logs.

Known steps sit in a fixed stage order. A name that is not in that template
still appears, in the fallback column. Replay reads this data only.
"""

from __future__ import annotations

import json

from dispute_copilot.fields import CASE_ID, Routing

NOT_RUN = ("review.submit", "record.precheck", "record.create")
_FAIL_OUTCOMES = {"exhausted", "error", "failed"}
_INJECTION_EVENTS = {"injection_detected", "security.untrusted_instruction"}
_COLUMNS = (
    ("intake", "Intake", False),
    ("evidence", "Evidence", False),
    ("ledger", "Ledger", False),
    ("rules", "Rules", False),
    ("draft", "Draft", False),
    ("checklist", "Checklist", False),
    ("human", "Human review", True),
    ("other", "Other steps", False),
)
_LABELS = {
    "case": "Whole case",
    "intake.parse": "Read the intake",
    "documents.followups": "Read follow-ups",
    "rules.classify": "Classify the complaint",
    "policy.select": "Choose the policy",
    "rules.routing": "Choose a route",
    "citations.build": "Build citations",
    "ledger.lookup": "Look up the ledger",
    "contract.validate": "Check the contract",
    "redaction.check": "Check for private text",
    "draft.write": "Write the draft",
    "review.submit": "Submit for review",
    "record.precheck": "Check before saving",
    "record.create": "Save the record",
    "injection.scan": "Scan untrusted text",
    "model.call": "Model check",
}


def build_paths(
    records: list[dict],
    log_rows: list[dict],
    approval: list[dict] | None = None,
) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        grouped.setdefault((_text(item.get(CASE_ID)), _text(item.get("run_id"))), []).append(item)
    humans = {
        _text(item.get("case")): _text(item.get("human")) or "unmeasured"
        for item in (approval or [])
        if isinstance(item, dict)
    }
    paths = []
    for case, run in sorted(grouped):
        items = grouped[(case, run)]
        spans = [item for item in items if item.get("kind") == "span"]
        if not spans:
            continue
        events = [item for item in items if item.get("kind") == "event"]
        nodes = [_node(span, events, log_rows) for span in _ordered(spans)]
        _mark_injection(nodes, events, log_rows)
        seen = {node["name"] for node in nodes}
        for name in NOT_RUN:
            if name in seen:
                continue
            nodes.append(_absent(name))
        ended = ""
        for node in nodes:
            if node["name"] == "case" and _route_text(node["attrs"].get("route")):
                ended = _route_text(node["attrs"].get("route")) or ""
        if not ended:
            for node in nodes:
                if node["name"] == "rules.routing" and _route_text(node["attrs"].get("route")):
                    ended = _route_text(node["attrs"].get("route")) or ""
                    break
        paths.append(
            {
                "case": case,
                "run": run,
                "ended": ended,
                "injection": any("injection" in node["marks"] for node in nodes),
                "nodes": nodes,
                "diagram": _diagram(nodes, events, ended, humans.get(case, "unmeasured")),
            }
        )
    return paths


def path_markup(paths: list[dict]) -> str:
    payload = json.dumps(paths, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        "<p class=\"note\">Pick a case. Replay follows the saved order on this page.</p>"
        "<div class=\"path-grid\">"
        + _pane("a")
        + _pane("b")
        + "</div>"
        f'<script type="application/json" id="path-data">{payload}</script>'
    )


def _pane(side: str) -> str:
    return (
        f'<div class="path-pane" id="path-pane-{side}">'
        '<div class="path-picks">'
        f'<label>Case <select id="path-case-{side}" aria-label="case"></select></label>'
        f'<label>Run <select id="path-run-{side}" aria-label="run"></select></label>'
        "</div>"
        f'<p class="path-summary" id="path-summary-{side}"></p>'
        '<div class="zoom">'
        f'<button type="button" data-zoom="in" aria-label="Zoom in">+</button>'
        f'<button type="button" data-zoom="out" aria-label="Zoom out">−</button>'
        f'<button type="button" data-zoom="fit" aria-label="Fit to width">Fit</button>'
        "</div>"
        f'<div class="flow-scroll" id="path-scroll-{side}">'
        f'<div class="flow-scale" id="path-scale-{side}"></div>'
        "</div>"
        f'<div class="replay" id="path-replay-{side}">'
        '<button type="button" data-act="play">Play</button>'
        '<button type="button" data-act="pause">Pause</button>'
        '<button type="button" data-act="restart">Restart</button>'
        '<button type="button" data-speed="1" aria-pressed="true">1x</button>'
        '<button type="button" data-speed="2">2x</button>'
        '<button type="button" data-speed="5">5x</button>'
        f'<label class="scrub">Position <input id="path-scrub-{side}" type="range" min="0" max="0" value="0" aria-label="replay position"></label>'
        "</div>"
        f'<pre class="path-detail" id="path-detail-{side}"></pre>'
        "</div>"
    )


def _diagram(nodes: list[dict], events: list[dict], ended: str, human: str) -> dict:
    used: set[int] = set()
    built: dict[str, dict] = {}
    for name, column, slot, stack, label in _template_rows():
        source = _take_name(nodes, used, name)
        built[name] = _step(name, label, column, slot, stack, source)
    for dependency, slot in (("transactions", 0), ("merchant", 1)):
        call = _take_call(nodes, used, dependency)
        attempts = _take_attempts(nodes, used, dependency)
        built[dependency] = _dependency_step(dependency, slot, call, attempts)
    checklist_span = _take_checklist(nodes, used)
    checklist_event = _checklist_event(events)
    built["checklist"] = _checklist_step(checklist_span, checklist_event)
    built["approval"] = _approval_step(human)
    extras = _extras(nodes, used, checklist_span is not None or checklist_event is not None)
    _routing_outcomes(built, ended)
    _injection_branch(built)
    _failure_edge(built, ended)
    ordered = _visual_order(built, extras)
    edges = _edges(built, ended)
    beats = _beats(nodes, built, extras)
    columns = [
        {"id": column, "title": title, "human": human_stage}
        for column, title, human_stage in _COLUMNS
        if column != "other" or extras
    ]
    return {"columns": columns, "nodes": ordered, "edges": edges, "beats": beats}


def _template_rows() -> tuple[tuple[str, str, int, int, str], ...]:
    return (
        ("case", "intake", 0, 0, _LABELS["case"]),
        ("intake.parse", "intake", 0, 1, _LABELS["intake.parse"]),
        ("documents.followups", "intake", 0, 2, _LABELS["documents.followups"]),
        ("ledger.lookup", "ledger", 0, 0, _LABELS["ledger.lookup"]),
        ("rules.classify", "rules", 0, 0, _LABELS["rules.classify"]),
        ("policy.select", "rules", 0, 1, _LABELS["policy.select"]),
        ("citations.build", "rules", 0, 2, _LABELS["citations.build"]),
        ("rules.routing", "rules", 0, 3, _LABELS["rules.routing"]),
        ("injection.scan", "rules", 0, 4, _LABELS["injection.scan"]),
        ("draft.write", "draft", 0, 0, _LABELS["draft.write"]),
        ("contract.validate", "draft", 0, 1, _LABELS["contract.validate"]),
        ("redaction.check", "draft", 0, 2, _LABELS["redaction.check"]),
        ("review.submit", "human", 0, 0, _LABELS["review.submit"]),
        ("record.precheck", "human", 0, 2, _LABELS["record.precheck"]),
        ("record.create", "human", 0, 3, _LABELS["record.create"]),
    )


def _step(name: str, label: str, column: str, slot: int, stack: int, source: dict | None) -> dict:
    if source is None:
        return _blank(name, label, column, slot, stack, "step")
    node = _from_source(name, label, column, slot, stack, "step", source)
    return node


def _dependency_step(dependency: str, slot: int, call: dict | None, attempts: list[dict]) -> dict:
    label = "Check transactions" if dependency == "transactions" else "Check merchant"
    source = call
    if source is None and attempts:
        source = attempts[0]
    if source is None:
        node = _blank(dependency, label, "evidence", slot, 1, "step")
    else:
        node = _from_source(dependency, label, "evidence", slot, 1, "step", source)
        if call is not None:
            node["name"] = call["name"]
            node["marks"] = list(call["marks"])
            node["duration_ms"] = call["duration_ms"]
            node["attrs"] = call["attrs"]
            node["logs"] = list(call["logs"])
            node["summary"] = call["summary"]
            node["record_status"] = call["record_status"]
    chips = []
    for attempt in attempts:
        code = attempt["attrs"].get("http_status")
        chips.append(
            {
                "code": code if isinstance(code, int) and not isinstance(code, bool) else None,
                "ms": attempt["duration_ms"],
                "fail": "fail" in attempt["marks"],
                "retry": "retry" in attempt["marks"],
                "label": _chip_label(code, attempt["duration_ms"]),
            }
        )
    node["chips"] = chips
    node["loop"] = any(chip["retry"] for chip in chips)
    if call is None and any(chip["fail"] for chip in chips):
        node["marks"] = [mark for mark in node["marks"] if mark != "ok"]
        if "fail" not in node["marks"]:
            node["marks"].insert(0, "fail")
    return node


def _checklist_step(span: dict | None, event: dict | None) -> dict:
    label = "Run the checklist"
    if span is None and event is None:
        return _blank("checklist", label, "checklist", 0, 0, "step")
    source = span or _absent("checklist")
    node = _from_source("checklist", label, "checklist", 0, 0, "step", source)
    if span is not None:
        node["name"] = span["name"]
        node["ran"] = True
        node["duration_ms"] = span["duration_ms"]
        node["attrs"] = span["attrs"]
        node["logs"] = list(span["logs"])
        node["summary"] = span["summary"]
        node["record_status"] = span["record_status"]
        node["marks"] = [mark for mark in span["marks"] if mark != "not run"] or ["ok"]
    else:
        node["ran"] = True
        node["marks"] = ["ok"]
        node["duration_ms"] = None
    if event is not None:
        attrs = _attrs(event)
        passed = attrs.get("passed")
        if passed is True:
            node["marks"] = ["ok"]
            node["summary"] = "passed"
        elif passed is False:
            node["marks"] = ["fail"]
            node["summary"] = "failed"
        node["attrs"] = {**node["attrs"], **{key: _plain(value) for key, value in attrs.items()}}
    return node


def _approval_step(human: str) -> dict:
    node = _blank("approval", "Human approval", "human", 0, 1, "step")
    if human in {"approved", "rejected", "waiting"}:
        node["ran"] = True
        node["marks"] = ["fail"] if human == "rejected" else ["ok"]
        node["summary"] = human
    return node


def _extras(nodes: list[dict], used: set[int], checklist_ran: bool) -> list[dict]:
    extras = []
    model_stack = 1
    other_stack = 0
    for node in nodes:
        if id(node) in used or not node["ran"]:
            continue
        if _is_attempt(node):
            used.add(id(node))
            label = "Extra step" if not extras else f"Extra step {len(extras) + 1}"
            extras.append(_from_source(f"extra-{len(extras)}", label, "other", 0, other_stack, "step", node))
            other_stack += 1
            continue
        used.add(id(node))
        if node["name"] == "model.call" and checklist_ran:
            extras.append(_from_source("model.call", _LABELS["model.call"], "checklist", 0, model_stack, "step", node))
            model_stack += 1
            continue
        label = _LABELS.get(node["name"]) or ("Extra step" if not extras else f"Extra step {len(extras) + 1}")
        column = "other"
        extras.append(_from_source(f"extra-{len(extras)}", label, column, 0, other_stack, "step", node))
        other_stack += 1
    return extras


def _routing_outcomes(built: dict[str, dict], ended: str) -> None:
    for index, item in enumerate(Routing):
        words = item.value.replace("_", " ").split(" ")
        label = words[0][:1].upper() + words[0][1:] + "".join(" " + word for word in words[1:])
        chosen = ended == item.value.replace("_", " ")
        node = _blank("outcome-" + item.name.lower(), label, "rules", 1, 3 + index, "outcome")
        node["ran"] = chosen
        node["chosen"] = chosen
        node["faded"] = not chosen
        node["marks"] = ["chosen"] if chosen else ["not taken"]
        built[node["id"]] = node


def _injection_branch(built: dict[str, dict]) -> None:
    scan = built.get("injection.scan")
    carriers = [node for node in built.values() if "injection" in node.get("marks", []) and node.get("ran")]
    carrier = scan if scan and scan.get("ran") and "injection" in scan.get("marks", []) else None
    if carrier is None and carriers:
        carrier = carriers[0]
    if carrier is None:
        return
    for node in carriers:
        if node is not carrier:
            node["marks"] = [mark for mark in node["marks"] if mark != "injection"]
    if "injection" not in carrier["marks"]:
        carrier["marks"].append("injection")
    carrier["badge"] = "Injection"
    side_stack = 6 if carrier["column"] == "rules" else carrier["stack"]
    side_slot = 1 if carrier["column"] == "rules" else carrier["slot"] + 1
    side = _blank("treated-as-data", "Treated as data", carrier["column"], side_slot, side_stack, "side")
    side["ran"] = True
    side["marks"] = ["injection"]
    side["badge"] = "Injection"
    built[side["id"]] = side


def _failure_edge(built: dict[str, dict], ended: str) -> None:
    if not ended:
        return
    chosen = next((node for node in built.values() if node.get("chosen")), None)
    if chosen is None:
        return
    for key in ("transactions", "merchant"):
        node = built.get(key)
        if node and "fail" in node.get("marks", []):
            node["fallback"] = chosen["id"]


def _visual_order(built: dict[str, dict], extras: list[dict]) -> list[dict]:
    fork = _blank("fork-evidence", "Split", "evidence", 0, 0, "fork")
    join = _blank("join-evidence", "Join", "evidence", 0, 2, "join")
    fork["span"] = 2
    join["span"] = 2
    branch_ran = built["transactions"]["ran"] or built["merchant"]["ran"]
    fork["ran"] = branch_ran
    join["ran"] = branch_ran
    nodes = [fork, join, *built.values(), *extras]
    nodes.sort(key=lambda item: (_column_index(item["column"]), item["stack"], item["slot"], item["id"]))
    return nodes


def _edges(built: dict[str, dict], ended: str) -> list[dict]:
    spine = [
        ("case", "intake.parse"),
        ("intake.parse", "documents.followups"),
        ("documents.followups", "fork-evidence"),
        ("fork-evidence", "transactions"),
        ("fork-evidence", "merchant"),
        ("transactions", "join-evidence"),
        ("merchant", "join-evidence"),
        ("join-evidence", "ledger.lookup"),
        ("ledger.lookup", "rules.classify"),
        ("rules.classify", "policy.select"),
        ("policy.select", "citations.build"),
        ("citations.build", "rules.routing"),
        ("rules.routing", "injection.scan"),
        ("injection.scan", "draft.write"),
        ("draft.write", "contract.validate"),
        ("contract.validate", "redaction.check"),
        ("redaction.check", "checklist"),
        ("checklist", "review.submit"),
        ("review.submit", "approval"),
        ("approval", "record.precheck"),
        ("record.precheck", "record.create"),
    ]
    nodes = {node["id"]: node for node in built.values()}
    nodes["fork-evidence"] = {"id": "fork-evidence", "ran": built["transactions"]["ran"] or built["merchant"]["ran"]}
    nodes["join-evidence"] = {"id": "join-evidence", "ran": nodes["fork-evidence"]["ran"]}
    edges = []
    for source, target in spine:
        edges.append({"from": source, "to": target, "kind": _edge_kind(nodes.get(source), nodes.get(target))})
    for item in Routing:
        target = "outcome-" + item.name.lower()
        chosen = ended == item.value.replace("_", " ")
        edges.append({"from": "rules.routing", "to": target, "kind": "flow" if chosen else "faint"})
    side = built.get("treated-as-data")
    if side is not None:
        carrier = next(
            (node for node in built.values() if node.get("badge") == "Injection" and node["id"] != side["id"]),
            None,
        )
        if carrier is not None:
            edges.append({"from": carrier["id"], "to": side["id"], "kind": "flow"})
    for key in ("transactions", "merchant"):
        node = built.get(key)
        if node and node.get("fallback"):
            edges.append({"from": node["id"], "to": node["fallback"], "kind": "dashed"})
    return edges


def _beats(nodes: list[dict], built: dict[str, dict], extras: list[dict]) -> list[dict]:
    by_source = {}
    for node in list(built.values()) + extras:
        source_id = node.get("source")
        if source_id:
            by_source.setdefault(source_id, node)
    chip_cursor = {"transactions": 0, "merchant": 0}
    beats: list[dict] = []
    for node in nodes:
        if not node["ran"]:
            continue
        dependency = node["attrs"].get("dependency")
        if _is_attempt(node) and dependency in chip_cursor:
            index = chip_cursor[dependency]
            chip_cursor[dependency] = index + 1
            chips = built[dependency]["chips"]
            if index < len(chips):
                chips[index]["beat"] = len(beats)
            beats.append({"node": dependency, "chip": index})
            continue
        match = by_source.get(node.get("source"))
        if match is None:
            continue
        if match.get("beat") is None:
            match["beat"] = len(beats)
        beats.append({"node": match["id"], "chip": None})
    routing = built.get("rules.routing")
    if routing and routing.get("beat") is not None:
        for node in built.values():
            if node.get("chosen"):
                node["beat"] = routing["beat"]
    carrier_beat = None
    for node in list(built.values()) + extras:
        if node.get("badge") == "Injection" and node["id"] != "treated-as-data" and node.get("beat") is not None:
            carrier_beat = node["beat"]
    side = built.get("treated-as-data")
    if side is not None and carrier_beat is not None:
        side["beat"] = carrier_beat
    for key in ("transactions", "merchant"):
        node = built.get(key)
        if node is None or node.get("beat") is not None:
            continue
        for chip in node["chips"]:
            if chip.get("beat") is not None:
                node["beat"] = chip["beat"]
                break
    return beats


def _edge_kind(source: dict | None, target: dict | None) -> str:
    if source and target and source.get("ran") and target.get("ran"):
        return "flow"
    return "faint"


def _from_source(
    node_id: str,
    label: str,
    column: str,
    slot: int,
    stack: int,
    kind: str,
    source: dict,
) -> dict:
    node = _blank(node_id, label, column, slot, stack, kind)
    node["name"] = source["name"]
    node["ran"] = bool(source["ran"])
    node["duration_ms"] = source["duration_ms"]
    node["marks"] = list(source["marks"])
    node["summary"] = source["summary"]
    node["attrs"] = source["attrs"]
    node["logs"] = list(source["logs"])
    node["record_status"] = source["record_status"]
    node["source"] = source.get("source") or source.get("span_id") or node_id
    return node


def _blank(node_id: str, label: str, column: str, slot: int, stack: int, kind: str) -> dict:
    return {
        "id": node_id,
        "name": node_id,
        "label": label,
        "column": column,
        "slot": slot,
        "stack": stack,
        "span": 1,
        "kind": kind,
        "ran": False,
        "duration_ms": None,
        "marks": ["not run"],
        "summary": "",
        "attrs": {},
        "logs": [],
        "record_status": "",
        "chips": [],
        "loop": False,
        "chosen": False,
        "faded": False,
        "badge": "",
        "beat": None,
        "source": "",
    }


def _absent(name: str) -> dict:
    return {
        "name": name,
        "ran": False,
        "duration_ms": None,
        "marks": ["not run"],
        "summary": "",
        "attrs": {},
        "logs": [],
        "record_status": "",
        "span_id": "",
        "source": "",
    }


def _take_name(nodes: list[dict], used: set[int], name: str) -> dict | None:
    for node in nodes:
        if id(node) in used or not node["ran"] or node["name"] != name:
            continue
        used.add(id(node))
        return node
    return None


def _take_call(nodes: list[dict], used: set[int], dependency: str) -> dict | None:
    for node in nodes:
        if id(node) in used or not node["ran"]:
            continue
        if node["attrs"].get("dependency") != dependency or _is_attempt(node):
            continue
        if not str(node["name"]).endswith(".call"):
            continue
        used.add(id(node))
        return node
    return None


def _take_attempts(nodes: list[dict], used: set[int], dependency: str) -> list[dict]:
    found = []
    for node in nodes:
        if id(node) in used or not node["ran"]:
            continue
        if node["attrs"].get("dependency") != dependency or not _is_attempt(node):
            continue
        used.add(id(node))
        found.append(node)
    return found


def _take_checklist(nodes: list[dict], used: set[int]) -> dict | None:
    for node in nodes:
        if id(node) in used or not node["ran"]:
            continue
        if not _is_checklist_name(node["name"]):
            continue
        used.add(id(node))
        return node
    return None


def _checklist_event(events: list[dict]) -> dict | None:
    for item in events:
        if "checklist" in str(item.get("name") or ""):
            return item
    return None


def _is_checklist_name(name: str) -> bool:
    if "checklist" in name:
        return True
    return name.endswith("-review") and "submit" not in name


def _is_attempt(node: dict) -> bool:
    attempt = node["attrs"].get("attempt")
    return isinstance(attempt, int) and not isinstance(attempt, bool) and str(node["name"]).endswith(".attempt")


def _column_index(column: str) -> int:
    for index, (name, _title, _human) in enumerate(_COLUMNS):
        if name == column:
            return index
    return len(_COLUMNS)


def _chip_label(code: object, duration: float | None) -> str:
    status = str(code) if isinstance(code, int) and not isinstance(code, bool) else "—"
    return f"{status} · {_ms_text(duration)}"


def _ms_text(value: float | None) -> str:
    if value is None:
        return "not run"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text + " ms"


def _ordered(spans: list[dict]) -> list[dict]:
    by_id = {item.get("span_id"): item for item in spans if item.get("span_id")}

    def depth(item: dict, guard: int = 0) -> int:
        if guard > 8:
            return 8
        parent = item.get("parent_id")
        if not parent or parent not in by_id:
            return 0
        return 1 + depth(by_id[parent], guard + 1)

    return sorted(
        spans,
        key=lambda item: (
            str(item.get("start") or item.get("ts") or ""),
            depth(item),
            str(item.get("name") or ""),
            str(item.get("span_id") or ""),
        ),
    )


def _node(span: dict, events: list[dict], log_rows: list[dict]) -> dict:
    attrs = _attrs(span)
    plain = {key: _plain(value) for key, value in attrs.items()}
    return {
        "name": str(span.get("name") or ""),
        "ran": True,
        "duration_ms": _ms(span.get("duration_ms")),
        "marks": _marks(span, attrs),
        "summary": _summary(attrs),
        "attrs": plain,
        "logs": _lines(span, events, log_rows),
        "record_status": _text(span.get("status")),
        "span_id": _text(span.get("span_id")),
        "source": _text(span.get("span_id")) or str(span.get("name") or ""),
    }


def _marks(span: dict, attrs: dict) -> list[str]:
    marks: list[str] = []
    status = span.get("status")
    outcome = attrs.get("outcome")
    code = attrs.get("http_status")
    failed = status == "error" or outcome in _FAIL_OUTCOMES or attrs.get("timed_out") is True
    if isinstance(code, int) and not isinstance(code, bool) and code >= 500:
        failed = True
    attempt = attrs.get("attempt")
    retry = isinstance(attempt, int) and not isinstance(attempt, bool) and attempt > 1
    if failed:
        marks.append("fail")
    if retry:
        marks.append("retry")
    if span.get("name") == "injection.scan" or attrs.get("injection_detected") is True:
        marks.append("injection")
    route = _route_text(attrs.get("route"))
    if route:
        marks.append(route)
    if not marks:
        marks.append("ok")
    return marks


def _lines(span: dict, events: list[dict], log_rows: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    span_id = span.get("span_id")
    attempt = _attrs(span).get("attempt")
    dependency = _attrs(span).get("dependency")

    def add(item: dict) -> None:
        line = {
            "level": _text(item.get("level")) or "info",
            "event": _text(item.get("name")),
            "text": _line_text(item)[:240],
        }
        key = (line["level"], line["event"], line["text"])
        if key in seen:
            return
        seen.add(key)
        found.append(line)

    for item in events:
        parent = item.get("parent_id")
        if span_id and parent == span_id:
            add(item)
            continue
        event_attrs = _attrs(item)
        if (
            item.get("name") == "retry.backoff"
            and attempt is not None
            and event_attrs.get("attempt") == attempt
            and event_attrs.get("dependency") == dependency
        ):
            add(item)
    for item in log_rows:
        if not span_id:
            continue
        if item.get("span_id") == span_id or item.get("parent_id") == span_id:
            add(item)
    return found


def _mark_injection(nodes: list[dict], events: list[dict], log_rows: list[dict]) -> None:
    parents: set[str] = set()
    for item in list(events) + list(log_rows):
        attrs = _attrs(item)
        name = item.get("name")
        if name not in _INJECTION_EVENTS and attrs.get("injection_detected") is not True:
            continue
        parent = item.get("parent_id")
        if isinstance(parent, str):
            parents.add(parent)
    for node in nodes:
        if node.get("span_id") not in parents and "injection" not in node["marks"]:
            continue
        if "injection" not in node["marks"]:
            node["marks"].append("injection")
        node["marks"] = [mark for mark in node["marks"] if mark != "ok"]


def _summary(attrs: dict) -> str:
    bits = []
    if attrs.get("dependency"):
        bits.append(str(attrs["dependency"]))
    if attrs.get("attempt") is not None:
        bits.append(f"attempt {attrs['attempt']}")
    if attrs.get("http_status") is not None:
        bits.append(f"status {attrs['http_status']}")
    if attrs.get("outcome"):
        bits.append(str(attrs["outcome"]))
    route = _route_text(attrs.get("route"))
    if route:
        bits.append(route)
    return " · ".join(bits)


def _line_text(item: dict) -> str:
    message = item.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return _summary(_attrs(item))


def _route_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for item in Routing:
        if value == item:
            return item.value.replace("_", " ")
    return None


def _attrs(item: dict) -> dict:
    attrs = item.get("attrs")
    return attrs if isinstance(attrs, dict) else {}


def _plain(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return str(value)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _ms(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


PAGE_SCRIPT = r"""
<script>
(function () {
  var root = document.documentElement;
  var toggle = document.getElementById("theme-toggle");
  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function themeNow() {
    var chosen = root.getAttribute("data-theme");
    if (chosen === "dark" || chosen === "light") return chosen;
    return systemDark() ? "dark" : "light";
  }
  function applyTheme(theme) {
    if (theme === "dark" || theme === "light") root.setAttribute("data-theme", theme);
    if (toggle) toggle.textContent = themeNow() === "dark" ? "Light" : "Dark";
  }
  var stored = null;
  try { stored = localStorage.getItem("status-theme"); } catch (err) { stored = null; }
  if (stored === "dark" || stored === "light") applyTheme(stored);
  else if (toggle) toggle.textContent = themeNow() === "dark" ? "Light" : "Dark";
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = themeNow() === "dark" ? "light" : "dark";
      try { localStorage.setItem("status-theme", next); } catch (err) {}
      applyTheme(next);
    });
  }

  var count = document.getElementById("log-count");
  if (count && document.getElementById("f-run")) {
    var filterIds = ["f-run", "f-case", "f-span", "f-event", "f-level"];
    function applyLogs() {
      var run = document.getElementById("f-run").value.trim().toLowerCase();
      var caseId = document.getElementById("f-case").value.trim().toLowerCase();
      var span = document.getElementById("f-span").value.trim().toLowerCase();
      var eventName = document.getElementById("f-event").value.trim().toLowerCase();
      var level = document.getElementById("f-level").value.trim().toLowerCase();
      var rows = document.querySelectorAll("#log-table tbody tr");
      var shown = 0;
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var show = (!run || (row.dataset.run || "").toLowerCase().indexOf(run) !== -1)
          && (!caseId || (row.dataset.case || "").toLowerCase().indexOf(caseId) !== -1)
          && (!span || (row.dataset.span || "").toLowerCase().indexOf(span) !== -1)
          && (!eventName || (row.dataset.event || "").toLowerCase().indexOf(eventName) !== -1)
          && (!level || (row.dataset.level || "").toLowerCase() === level);
        row.hidden = !show;
        if (show) shown += 1;
      }
      var older = "";
      var hidden = count.getAttribute("data-hidden");
      if (hidden && hidden !== "0") {
        older = ", showing " + count.getAttribute("data-limit") + ", " + hidden + " older lines left out";
      }
      count.textContent = shown + " of " + count.getAttribute("data-total") + " lines" + older;
    }
    for (var j = 0; j < filterIds.length; j++) {
      document.getElementById(filterIds[j]).addEventListener("input", applyLogs);
      document.getElementById(filterIds[j]).addEventListener("change", applyLogs);
    }
    applyLogs();
  }

  var dataEl = document.getElementById("path-data");
  if (!dataEl) return;
  var paths = [];
  try { paths = JSON.parse(dataEl.textContent || "[]"); } catch (err) { paths = []; }
  var svgNS = (function () {
    var probe = document.createElement("div");
    probe.innerHTML = "<svg></svg>";
    return probe.firstChild.namespaceURI;
  })();
  var COL_ORDER = ["intake", "evidence", "ledger", "rules", "draft", "checklist", "human", "other"];
  var NODE_W = 168;
  var SLOT_GAP = 16;
  var COL_GAP = 48;
  var ROW_GAP = 28;

  function cases() {
    var list = [];
    paths.forEach(function (path) { if (list.indexOf(path.case) === -1) list.push(path.case); });
    return list;
  }
  function runsFor(caseId) {
    var list = [];
    paths.forEach(function (path) {
      if (path.case === caseId && list.indexOf(path.run) === -1) list.push(path.run);
    });
    return list;
  }
  function findPath(caseId, runId) {
    for (var i = 0; i < paths.length; i++) {
      if (paths[i].case === caseId && paths[i].run === runId) return paths[i];
    }
    return null;
  }
  function fillSelect(select, values, current) {
    while (select.firstChild) select.removeChild(select.firstChild);
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value || "(none)";
      if (value === current) option.selected = true;
      select.appendChild(option);
    });
  }
  function formatMs(value) {
    if (value === null || value === undefined) return "not run";
    var text = String(Math.round(Number(value) * 1000) / 1000);
    return text + " ms";
  }
  function el(name) { return document.createElementNS(svgNS, name); }
  function columnSet() {
    var needOther = false;
    paths.forEach(function (path) {
      (path.diagram && path.diagram.nodes || []).forEach(function (node) {
        if (node.column === "other") needOther = true;
      });
    });
    var titles = {};
    paths.forEach(function (path) {
      (path.diagram && path.diagram.columns || []).forEach(function (column) { titles[column.id] = column; });
    });
    return COL_ORDER.filter(function (id) { return id !== "other" || needOther; }).map(function (id) {
      return titles[id] || {id: id, title: id, human: id === "human"};
    });
  }
  function slotsFor(columns) {
    var slots = {intake: 1, evidence: 2, ledger: 1, rules: 2, draft: 1, checklist: 1, human: 1, other: 1};
    paths.forEach(function (path) {
      (path.diagram && path.diagram.nodes || []).forEach(function (node) {
        var need = (node.slot || 0) + (node.kind === "side" ? 1 : 1);
        if ((slots[node.column] || 1) < (node.slot || 0) + 1) slots[node.column] = (node.slot || 0) + 1;
        if (need > (slots[node.column] || 1) && node.span > 1) slots[node.column] = Math.max(slots[node.column] || 1, node.span);
      });
    });
    columns.forEach(function (column) { if (!slots[column.id]) slots[column.id] = 1; });
    return slots;
  }
  function nodeHeight(node) {
    if (node.kind === "fork" || node.kind === "join") return 26;
    return 92 + ((node.chips && node.chips.length) || 0) * 18;
  }
  function statusText(node) {
    if (node.faded) return "not taken";
    if (!node.ran) return "not run";
    var bits = [];
    (node.marks || []).forEach(function (mark) {
      if (bits.indexOf(mark) === -1) bits.push(mark);
    });
    if (!bits.length) bits.push("ok");
    return bits.join(" · ");
  }
  function detailText(node) {
    var lines = [node.label || "", node.name || ""];
    lines.push(node.ran ? formatMs(node.duration_ms) : "not run");
    if (node.record_status) lines.push("status=" + node.record_status);
    lines.push(statusText(node));
    if (node.summary) lines.push(node.summary);
    (node.chips || []).forEach(function (chip) { lines.push(chip.label || ""); });
    var attrs = node.attrs || {};
    Object.keys(attrs).forEach(function (key) { lines.push(key + "=" + String(attrs[key])); });
    (node.logs || []).forEach(function (row) { lines.push(row.level + " " + row.event + " " + row.text); });
    return lines.join(String.fromCharCode(10));
  }
  function summaryText(path) {
    if (!path) return "No saved steps.";
    var bits = [];
    if (path.ended) bits.push("Ended in " + path.ended);
    if (path.injection) bits.push("Injection flagged");
    if (!bits.length) bits.push("Saved steps");
    return bits.join(". ");
  }
  function place(path, columns, slots) {
    var colX = {};
    var colW = {};
    var cursor = 20;
    columns.forEach(function (column) {
      if (column.human) cursor += 36;
      colX[column.id] = cursor;
      var count = slots[column.id] || 1;
      colW[column.id] = count * NODE_W + (count - 1) * SLOT_GAP;
      cursor += colW[column.id] + COL_GAP;
    });
    var rows = {};
    (path.diagram.nodes || []).forEach(function (node) {
      var key = node.column;
      if (!rows[key]) rows[key] = {};
      var stack = node.stack || 0;
      if (!rows[key][stack]) rows[key][stack] = [];
      rows[key][stack].push(node);
    });
    var pos = {};
    var bottom = 72;
    columns.forEach(function (column) {
      var stacks = Object.keys(rows[column.id] || {}).map(function (key) { return Number(key); }).sort(function (a, b) { return a - b; });
      var y = 48;
      stacks.forEach(function (stack) {
        var group = rows[column.id][stack];
        var height = 0;
        group.forEach(function (node) { height = Math.max(height, nodeHeight(node)); });
        group.forEach(function (node) {
          var width = node.span > 1 ? (node.span * NODE_W + (node.span - 1) * SLOT_GAP) : NODE_W;
          var x = colX[column.id] + (node.slot || 0) * (NODE_W + SLOT_GAP);
          if (node.kind === "fork" || node.kind === "join") {
            width = 22;
            x = colX[column.id] + (colW[column.id] - 22) / 2;
          }
          pos[node.id] = {x: x, y: y, w: width, h: node.kind === "fork" || node.kind === "join" ? 22 : height, column: column.id, node: node};
          bottom = Math.max(bottom, y + height);
        });
        y += height + ROW_GAP;
      });
    });
    return {pos: pos, width: cursor + 8, height: bottom + 24, colX: colX, colW: colW};
  }
  function curve(a, b) {
    if (!a || !b) return "";
    if (a.column === b.column && b.y >= a.y) {
      var x = a.x + a.w / 2;
      return "M" + x + "," + (a.y + a.h) + " L" + x + "," + b.y;
    }
    var x1 = a.x + a.w;
    var y1 = a.y + a.h / 2;
    var x2 = b.x;
    var y2 = b.y + b.h / 2;
    if (x2 < x1) {
      x1 = a.x + a.w / 2;
      y1 = a.y + a.h;
      x2 = b.x + b.w / 2;
      y2 = b.y;
    }
    var mid = (x1 + x2) / 2;
    return "M" + x1 + "," + y1 + " C" + mid + "," + y1 + " " + mid + "," + y2 + " " + x2 + "," + y2;
  }
  function marker(defs, id, tone) {
    var mark = el("marker");
    mark.setAttribute("id", id);
    mark.setAttribute("viewBox", "0 0 10 10");
    mark.setAttribute("refX", "8");
    mark.setAttribute("refY", "5");
    mark.setAttribute("markerWidth", "8");
    mark.setAttribute("markerHeight", "8");
    mark.setAttribute("orient", "auto");
    var head = el("path");
    head.setAttribute("d", "M0,0 L10,5 L0,10 Z");
    head.setAttribute("class", "arrow-head arrow-" + tone);
    mark.appendChild(head);
    defs.appendChild(mark);
  }
  function addPath(parent, d, kind, id) {
    var edge = el("path");
    edge.setAttribute("d", d);
    edge.setAttribute("class", "edge edge-" + kind);
    if (id) edge.setAttribute("data-edge", id);
    edge.setAttribute("fill", "none");
    parent.appendChild(edge);
    return edge;
  }
  function draw(host, detail, path, shown, highlight, columns, slots) {
    while (host.firstChild) host.removeChild(host.firstChild);
    if (!path || !path.diagram) {
      host.textContent = "No saved steps.";
      return null;
    }
    var laid = place(path, columns, slots);
    var svg = el("svg");
    svg.setAttribute("class", "flow");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Workflow for " + (path.case || "case"));
    svg.setAttribute("width", String(laid.width));
    svg.setAttribute("height", String(laid.height));
    svg.setAttribute("viewBox", "0 0 " + laid.width + " " + laid.height);
    var defs = el("defs");
    marker(defs, "arrow-flow-" + host.id, "flow");
    marker(defs, "arrow-faint-" + host.id, "faint");
    marker(defs, "arrow-dashed-" + host.id, "dashed");
    svg.appendChild(defs);
    columns.forEach(function (column) {
      if (!column.human || laid.colX[column.id] === undefined) return;
      var band = el("rect");
      band.setAttribute("class", "human-band");
      band.setAttribute("x", String(laid.colX[column.id] - 16));
      band.setAttribute("y", "8");
      band.setAttribute("width", String(laid.colW[column.id] + 32));
      band.setAttribute("height", String(laid.height - 16));
      band.setAttribute("rx", "18");
      svg.appendChild(band);
    });
    columns.forEach(function (column) {
      if (laid.colX[column.id] === undefined) return;
      var title = el("text");
      title.setAttribute("class", "col-title");
      title.setAttribute("x", String(laid.colX[column.id]));
      title.setAttribute("y", "28");
      title.textContent = column.title;
      svg.appendChild(title);
    });
    var edges = el("g");
    (path.diagram.edges || []).forEach(function (edge) {
      var from = laid.pos[edge.from];
      var to = laid.pos[edge.to];
      if (!from || !to) return;
      var kind = edge.kind || "faint";
      var target = (path.diagram.nodes || []).filter(function (node) { return node.id === edge.to; })[0];
      var source = (path.diagram.nodes || []).filter(function (node) { return node.id === edge.from; })[0];
      if (shown < (path.diagram.beats || []).length) {
        var targetBeat = target && target.beat !== null && target.beat !== undefined ? target.beat : null;
        var sourceBeat = source && source.beat !== null && source.beat !== undefined ? source.beat : null;
        var revealed = (targetBeat === null || targetBeat < shown) && (sourceBeat === null || sourceBeat < shown);
        if (!revealed && kind === "flow") kind = "faint";
      }
      var drawn = addPath(edges, curve(from, to), kind, edge.from + ">" + edge.to);
      drawn.setAttribute("marker-end", "url(#arrow-" + kind + "-" + host.id + ")");
    });
    svg.appendChild(edges);
    (path.diagram.nodes || []).forEach(function (node) {
      var box = laid.pos[node.id];
      if (!box) return;
      if (node.kind === "fork" || node.kind === "join") {
        var shape = el("polygon");
        var cx = box.x + box.w / 2;
        var cy = box.y + box.h / 2;
        shape.setAttribute("points", cx + "," + box.y + " " + (box.x + box.w) + "," + cy + " " + cx + "," + (box.y + box.h) + " " + box.x + "," + cy);
        shape.setAttribute("class", "join-shape");
        shape.setAttribute("aria-hidden", "true");
        svg.appendChild(shape);
        return;
      }
      if (node.loop) {
        var loop = el("path");
        var lx = box.x + box.w - 4;
        var ly = box.y + 6;
        loop.setAttribute("d", "M" + lx + "," + (ly + 16) + " C" + (lx + 18) + "," + ly + " " + (lx + 18) + "," + (ly + 30) + " " + lx + "," + (ly + 20));
        loop.setAttribute("class", "loop-arrow");
        loop.setAttribute("fill", "none");
        svg.appendChild(loop);
      }
      var holder = el("foreignObject");
      holder.setAttribute("x", String(box.x));
      holder.setAttribute("y", String(box.y));
      holder.setAttribute("width", String(box.w));
      holder.setAttribute("height", String(box.h));
      var button = document.createElement("button");
      button.type = "button";
      var future = node.beat !== null && node.beat !== undefined && node.beat >= shown;
      var current = false;
      if (highlight && shown > 0) {
        var beat = (path.diagram.beats || [])[shown - 1];
        current = beat && beat.node === node.id;
      }
      var className = "node";
      if (future) className += " is-future";
      if (current) className += " is-current";
      if (node.faded) className += " is-faded";
      if (!node.ran) className += " is-skipped";
      (node.marks || []).forEach(function (mark) {
        if (mark === "fail") className += " is-fail";
        if (mark === "retry") className += " is-retry";
        if (mark === "injection") className += " is-injection";
      });
      if (node.chosen) className += " is-chosen";
      button.className = className;
      button.setAttribute("aria-label", (node.label || "Step") + ", " + statusText(node) + ", " + (node.ran ? formatMs(node.duration_ms) : "not run"));
      var stripe = document.createElement("span");
      stripe.className = "stripe";
      stripe.setAttribute("aria-hidden", "true");
      button.appendChild(stripe);
      var title = document.createElement("span");
      title.className = "node-label";
      title.textContent = node.label || "";
      button.appendChild(title);
      var meta = document.createElement("span");
      meta.className = "node-meta";
      meta.textContent = node.ran ? formatMs(node.duration_ms) : "not run";
      button.appendChild(meta);
      var state = document.createElement("span");
      state.className = "node-state";
      state.textContent = statusText(node);
      button.appendChild(state);
      if (node.badge) {
        var badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = node.badge;
        button.appendChild(badge);
      }
      (node.chips || []).forEach(function (chip) {
        var pill = document.createElement("span");
        var chipClass = "chip";
        if (chip.fail) chipClass += " is-fail";
        if (chip.retry) chipClass += " is-retry";
        if (highlight && shown > 0 && chip.beat === shown - 1) chipClass += " is-current";
        if (chip.beat !== null && chip.beat !== undefined && chip.beat >= shown) chipClass += " is-future";
        pill.className = chipClass;
        pill.textContent = chip.label || "";
        button.appendChild(pill);
      });
      button.addEventListener("click", function () { detail.textContent = detailText(node); });
      holder.appendChild(button);
      svg.appendChild(holder);
    });
    host.appendChild(svg);
    return laid;
  }
  function bind(side, index) {
    var caseSelect = document.getElementById("path-case-" + side);
    var runSelect = document.getElementById("path-run-" + side);
    var host = document.getElementById("path-scale-" + side);
    var scroll = document.getElementById("path-scroll-" + side);
    var detail = document.getElementById("path-detail-" + side);
    var summary = document.getElementById("path-summary-" + side);
    var range = document.getElementById("path-scrub-" + side);
    var replay = document.getElementById("path-replay-" + side);
    var pane = document.getElementById("path-pane-" + side);
    var columns = columnSet();
    var slots = slotsFor(columns);
    var state = {timer: null, raf: null, speed: 1, playing: false, paused: false, zoom: 1, laid: null};
    function current() { return findPath(caseSelect.value, runSelect.value); }
    function clearMotion() {
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      if (state.raf) { cancelAnimationFrame(state.raf); state.raf = null; }
    }
    function applyZoom() {
      host.style.transform = "scale(" + state.zoom + ")";
      host.style.transformOrigin = "top left";
      var svg = host.querySelector("svg");
      if (!svg) return;
      var width = Number(svg.getAttribute("width")) || 0;
      var height = Number(svg.getAttribute("height")) || 0;
      host.style.width = Math.ceil(width * state.zoom) + "px";
      host.style.height = Math.ceil(height * state.zoom) + "px";
    }
    function show(path, shown, animate) {
      if (!path) {
        summary.textContent = "No saved steps.";
        host.textContent = "No saved steps.";
        return;
      }
      summary.textContent = summaryText(path);
      var total = (path.diagram.beats || []).length;
      range.max = String(total);
      range.value = String(Math.max(0, Math.min(shown, total)));
      state.laid = draw(host, detail, path, Number(range.value), state.playing || state.paused, columns, slots);
      applyZoom();
      if (animate && state.playing) travel(path, Number(range.value));
    }
    function travel(path, shown) {
      if (shown <= 0) return;
      var beats = path.diagram.beats || [];
      var curr = beats[shown - 1];
      var prev = shown > 1 ? beats[shown - 2] : null;
      if (!curr || !state.laid) return;
      var svg = host.querySelector("svg");
      if (!svg) return;
      var from = prev ? state.laid.pos[prev.node] : null;
      var to = state.laid.pos[curr.node];
      if (!to) return;
      var d = from && prev.node !== curr.node ? curve(from, to) : "";
      if (!d && to.node && to.node.loop) {
        var lx = to.x + to.w - 4;
        var ly = to.y + 6;
        d = "M" + lx + "," + (ly + 16) + " C" + (lx + 18) + "," + ly + " " + (lx + 18) + "," + (ly + 30) + " " + lx + "," + (ly + 20);
      }
      if (!d) {
        state.timer = setTimeout(step, Math.round(700 / state.speed));
        return;
      }
      var trail = addPath(svg, d, "travel", "");
      var dot = el("circle");
      dot.setAttribute("r", "5");
      dot.setAttribute("class", "travel-dot");
      svg.appendChild(dot);
      var len = trail.getTotalLength ? trail.getTotalLength() : 0;
      var start = performance.now();
      var dur = Math.max(120, Math.round(700 / state.speed));
      function frame(now) {
        if (!state.playing) return;
        var t = Math.min(1, (now - start) / dur);
        var point = len ? trail.getPointAtLength(len * t) : {x: to.x, y: to.y};
        dot.setAttribute("cx", String(point.x));
        dot.setAttribute("cy", String(point.y));
        if (t < 1) state.raf = requestAnimationFrame(frame);
        else state.timer = setTimeout(step, 30);
      }
      state.raf = requestAnimationFrame(frame);
    }
    function use(path, shown) {
      clearMotion();
      state.playing = false;
      state.paused = false;
      if (!path) { show(null, 0, false); return; }
      fillSelect(caseSelect, cases(), path.case);
      fillSelect(runSelect, runsFor(path.case), path.run);
      show(path, shown, false);
    }
    function step() {
      if (!state.playing) return;
      var path = current();
      var total = path && path.diagram ? path.diagram.beats.length : 0;
      var shown = Number(range.value);
      if (shown >= total) {
        state.playing = false;
        state.paused = true;
        show(path, shown, false);
        return;
      }
      show(path, shown + 1, true);
    }
    caseSelect.addEventListener("change", function () {
      var runs = runsFor(caseSelect.value);
      var path = findPath(caseSelect.value, runs[0]);
      use(path, path && path.diagram ? path.diagram.beats.length : 0);
    });
    runSelect.addEventListener("change", function () {
      var path = current();
      use(path, path && path.diagram ? path.diagram.beats.length : 0);
    });
    range.addEventListener("input", function () {
      clearMotion();
      state.playing = false;
      state.paused = true;
      show(current(), Number(range.value), false);
    });
    pane.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      var act = target.getAttribute("data-act");
      var speed = target.getAttribute("data-speed");
      var zoom = target.getAttribute("data-zoom");
      if (zoom) {
        var svg = host.querySelector("svg");
        var width = svg ? Number(svg.getAttribute("width")) : 0;
        if (zoom === "fit" && width && scroll.clientWidth) state.zoom = Math.min(1, (scroll.clientWidth - 8) / width);
        if (zoom === "in") state.zoom = Math.min(2, state.zoom * 1.2);
        if (zoom === "out") state.zoom = Math.max(0.35, state.zoom / 1.2);
        applyZoom();
        return;
      }
      if (speed) {
        state.speed = Number(speed) || 1;
        var speeds = replay.querySelectorAll("[data-speed]");
        for (var s = 0; s < speeds.length; s++) speeds[s].setAttribute("aria-pressed", speeds[s] === target ? "true" : "false");
        return;
      }
      if (act === "pause") {
        clearMotion();
        state.playing = false;
        state.paused = true;
        show(current(), Number(range.value), false);
        return;
      }
      if (act === "restart") {
        clearMotion();
        state.playing = false;
        state.paused = false;
        show(current(), 0, false);
        return;
      }
      if (act !== "play") return;
      clearMotion();
      var path = current();
      var total = path && path.diagram ? path.diagram.beats.length : 0;
      if (Number(range.value) >= total) range.value = "0";
      state.playing = true;
      state.paused = false;
      step();
    });
    var initial = (function () {
      if (!paths.length) return null;
      if (index === 0) return paths[0];
      for (var i = 1; i < paths.length; i++) if (paths[i].case !== paths[0].case) return paths[i];
      return paths[Math.min(1, paths.length - 1)];
    })();
    use(initial, initial && initial.diagram ? initial.diagram.beats.length : 0);
  }
  if (!paths.length) {
    var emptyA = document.getElementById("path-scale-a");
    var emptyB = document.getElementById("path-scale-b");
    if (emptyA) emptyA.textContent = "No saved steps.";
    if (emptyB) emptyB.textContent = "No saved steps.";
    return;
  }
  bind("a", 0);
  bind("b", 1);
})();
</script>
"""
