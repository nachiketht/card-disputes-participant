"""Request path from saved spans and logs.

Nodes follow the order the steps started. A step that was never recorded is
marked not run and has no duration. Replay reads this data only.
"""

from __future__ import annotations

import json

from dispute_copilot.fields import CASE_ID, Routing

NOT_RUN = ("review.submit", "record.precheck", "record.create")
_FAIL_OUTCOMES = {"exhausted", "error", "failed"}
_INJECTION_EVENTS = {"injection_detected", "security.untrusted_instruction"}


def build_paths(records: list[dict], log_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        grouped.setdefault((_text(item.get(CASE_ID)), _text(item.get("run_id"))), []).append(item)
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
            nodes.append(
                {
                    "name": name,
                    "ran": False,
                    "duration_ms": None,
                    "marks": ["not run"],
                    "summary": "",
                    "attrs": {},
                    "logs": [],
                    "record_status": "",
                }
            )
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
            }
        )
    return paths


def path_markup(paths: list[dict]) -> str:
    payload = json.dumps(paths, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        "<p class=\"note\">Nodes follow the saved steps in the order they started. "
        "A step that was not recorded is marked not run and has no duration. "
        "Replay walks those saved steps on this page. It does not contact a service and it does not write.</p>"
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
        f'<label>Case <select id="path-case-{side}"></select></label>'
        f'<label>Run <select id="path-run-{side}"></select></label>'
        "</div>"
        f'<p class="path-summary" id="path-summary-{side}"></p>'
        f'<div class="path-graph" id="path-graph-{side}"></div>'
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
    duration = _ms(span.get("duration_ms"))
    marks = _marks(span, attrs)
    logs = _lines(span, events, log_rows)
    summary = _summary(attrs)
    return {
        "name": str(span.get("name") or ""),
        "ran": True,
        "duration_ms": duration,
        "marks": marks,
        "summary": summary,
        "attrs": plain,
        "logs": logs,
        "record_status": _text(span.get("status")),
        "span_id": _text(span.get("span_id")),
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


PAGE_SCRIPT = """
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

  function cases() {
    var list = [];
    paths.forEach(function (path) {
      if (list.indexOf(path.case) === -1) list.push(path.case);
    });
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
  function ranCount(path) {
    var total = 0;
    if (!path) return 0;
    path.nodes.forEach(function (node) { if (node.ran) total += 1; });
    return total;
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
    return String(value) + " ms";
  }
  function addLine(parent, className, text) {
    var span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    parent.appendChild(span);
  }
  function detailText(node) {
    var lines = [node.name, node.ran ? formatMs(node.duration_ms) : "not run"];
    if (node.record_status) lines.push("status=" + node.record_status);
    lines.push((node.marks || []).join(", "));
    var attrs = node.attrs || {};
    Object.keys(attrs).forEach(function (key) {
      lines.push(key + "=" + String(attrs[key]));
    });
    (node.logs || []).forEach(function (row) {
      lines.push(row.level + " " + row.event + " " + row.text);
    });
    return lines.join(String.fromCharCode(10));
  }
  function summaryText(path) {
    if (!path) return "No saved steps.";
    var bits = [];
    if (path.ended) bits.push("Ended in " + path.ended);
    if (path.injection) bits.push("Injection flagged");
    if (!bits.length) bits.push("Saved steps in run order");
    return bits.join(". ");
  }
  function drawGraph(host, detail, path, shown, highlight) {
    while (host.firstChild) host.removeChild(host.firstChild);
    if (!path) {
      host.textContent = "No saved steps.";
      return;
    }
    var seen = 0;
    path.nodes.forEach(function (node, index) {
      if (index) {
        var arrow = document.createElement("div");
        arrow.className = "path-arrow";
        arrow.setAttribute("aria-hidden", "true");
        host.appendChild(arrow);
      }
      var button = document.createElement("button");
      button.type = "button";
      var future = node.ran && seen >= shown;
      var current = false;
      if (node.ran) {
        seen += 1;
        current = highlight && seen === shown && shown > 0;
      }
      var className = "path-node";
      if (future) className += " is-future";
      if (current) className += " is-current";
      (node.marks || []).forEach(function (mark) {
        if (mark === "fail") className += " is-fail";
        if (mark === "retry") className += " is-retry";
        if (mark === "injection") className += " is-injection";
        if (mark === "not run") className += " is-skipped";
      });
      if (path.ended && (node.marks || []).indexOf(path.ended) !== -1) className += " is-branch";
      button.className = className;
      addLine(button, "path-name", node.name);
      if (node.summary) addLine(button, "path-meta", node.summary);
      addLine(button, "path-ms", node.ran ? formatMs(node.duration_ms) : "not run");
      addLine(button, "path-marks", (node.marks || []).join(" · "));
      button.addEventListener("click", function () {
        detail.textContent = detailText(node);
      });
      host.appendChild(button);
    });
  }
  function initialPath(index) {
    if (!paths.length) return null;
    if (index === 0) return paths[0];
    for (var i = 1; i < paths.length; i++) {
      if (paths[i].case !== paths[0].case) return paths[i];
    }
    return paths[Math.min(1, paths.length - 1)];
  }
  function bind(side, index) {
    var caseSelect = document.getElementById("path-case-" + side);
    var runSelect = document.getElementById("path-run-" + side);
    var graph = document.getElementById("path-graph-" + side);
    var detail = document.getElementById("path-detail-" + side);
    var summary = document.getElementById("path-summary-" + side);
    var range = document.getElementById("path-scrub-" + side);
    var replay = document.getElementById("path-replay-" + side);
    var state = { timer: null, speed: 1, playing: false, paused: false };
    function current() { return findPath(caseSelect.value, runSelect.value); }
    function clearTimer() {
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
    }
    function show(path, shown) {
      if (!path) {
        summary.textContent = "No saved steps.";
        graph.textContent = "No saved steps.";
        return;
      }
      summary.textContent = summaryText(path);
      var total = ranCount(path);
      range.max = String(total);
      range.value = String(Math.max(0, Math.min(shown, total)));
      drawGraph(graph, detail, path, Number(range.value), state.playing || state.paused);
    }
    function use(path, shown) {
      clearTimer();
      state.playing = false;
      state.paused = false;
      if (!path) {
        show(null, 0);
        return;
      }
      fillSelect(caseSelect, cases(), path.case);
      fillSelect(runSelect, runsFor(path.case), path.run);
      show(path, shown);
    }
    caseSelect.addEventListener("change", function () {
      var runs = runsFor(caseSelect.value);
      use(findPath(caseSelect.value, runs[0]), ranCount(findPath(caseSelect.value, runs[0])));
    });
    runSelect.addEventListener("change", function () {
      var path = current();
      use(path, ranCount(path));
    });
    range.addEventListener("input", function () {
      clearTimer();
      state.playing = false;
      state.paused = true;
      show(current(), Number(range.value));
    });
    replay.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;
      var act = target.getAttribute("data-act");
      var speed = target.getAttribute("data-speed");
      if (speed) {
        state.speed = Number(speed) || 1;
        var speeds = replay.querySelectorAll("[data-speed]");
        for (var s = 0; s < speeds.length; s++) {
          speeds[s].setAttribute("aria-pressed", speeds[s] === target ? "true" : "false");
        }
        return;
      }
      if (act === "pause") {
        clearTimer();
        state.playing = false;
        state.paused = true;
        show(current(), Number(range.value));
        return;
      }
      if (act === "restart") {
        clearTimer();
        state.playing = false;
        state.paused = false;
        show(current(), 0);
        return;
      }
      if (act !== "play") return;
      clearTimer();
      var path = current();
      var total = ranCount(path);
      var shown = Number(range.value);
      if (shown >= total) shown = 0;
      state.playing = true;
      state.paused = false;
      function step() {
        if (!state.playing) return;
        if (shown >= total) {
          state.playing = false;
          state.paused = true;
          show(current(), shown);
          return;
        }
        shown += 1;
        show(current(), shown);
        state.timer = setTimeout(step, Math.round(700 / state.speed));
      }
      step();
    });
    use(initialPath(index), ranCount(initialPath(index)));
  }
  if (!paths.length) {
    var emptyA = document.getElementById("path-graph-a");
    var emptyB = document.getElementById("path-graph-b");
    if (emptyA) emptyA.textContent = "No saved steps.";
    if (emptyB) emptyB.textContent = "No saved steps.";
    return;
  }
  bind("a", 0);
  bind("b", 1);
})();
</script>
"""
