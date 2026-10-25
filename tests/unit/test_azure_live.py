"""The live pass uses a side ledger and never the demo files."""

import importlib.util
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSIDE_PATH = ROOT / "tools" / "azure_inside.py"
LIVE_PATH = ROOT / "tools" / "azure_live.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSIDE = _load(INSIDE_PATH, "azure_inside")
LIVE = _load(LIVE_PATH, "azure_live")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait(port: int) -> None:
    for _ in range(50):
        if _healthy(port):
            return
        time.sleep(0.2)
    raise AssertionError(f"nothing answered on {port}")


def test_gate_skips_a_fork_and_a_missing_sign_in(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("AZURE_FORK", "true")
    assert LIVE.gate() == 0
    assert capsys.readouterr().out.strip() == "run=skip"
    monkeypatch.setenv("AZURE_FORK", "false")
    for name in (
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_ID_VAR",
        "AZURE_TENANT_ID",
        "AZURE_TENANT_ID_VAR",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_SUBSCRIPTION_ID_VAR",
    ):
        monkeypatch.delenv(name, raising=False)
    assert LIVE.gate() == 0
    assert capsys.readouterr().out.strip() == "run=skip"


def test_plan_uses_a_side_ledger_and_does_not_write_demo_paths():
    commands = LIVE.mode_commands("abc") + LIVE.cleanup_commands("abc")
    text = "\n".join(command for _container, command in commands)
    assert "http://127.0.0.1:8765/health" in text
    assert "--url http://127.0.0.1:8766" in text
    assert "--state /tmp/ciabc.sqlite" in text
    assert "--submissions /tmp/ciabc/submissions" in text
    assert "--state /state" not in text
    assert "--submissions /submissions" not in text
    for banned in ("submit-review", "create-record", " runtime.py approve", " reset "):
        assert banned not in text
    with pytest.raises(SystemExit):
        INSIDE.guard(Path("/state/run.sqlite"))
    with pytest.raises(SystemExit):
        INSIDE.guard(Path("/submissions/drafts"))


def test_a_failed_precheck_still_cleans_up_and_does_not_start(tmp_path: Path):
    seen: list[tuple[str, str]] = []

    def fake(container: str, command: str) -> tuple[int, str]:
        seen.append((container, command))
        if " precheck " in command:
            return 0, "AZURE_CI_RESULT code=1\n"
        if " cleanup " in command:
            return 0, "AZURE_CI_RESULT code=0\n"
        return 0, ""

    code = LIVE.execute(tmp_path / "out", call=fake, run_id="abc", script=b"print(1)\n", gold=b"{}")
    assert code == 1
    text = "\n".join(command for _container, command in seen)
    assert " precheck " in text
    assert " cleanup " in text
    assert " start " not in text
    assert "submit-review" not in text
    assert "create-record" not in text


def test_workflow_files_keep_push_and_image_update_apart():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    azure = ci["jobs"]["azure"]
    assert azure["needs"] == ["lint", "test", "evaluate", "security", "secrets", "image"]
    assert azure["concurrency"]["group"] == "azure-live-container"
    assert azure["concurrency"]["cancel-in-progress"] is False
    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "containerapp update" not in ci_text
    assert "docker push" not in ci_text
    deploy = yaml.safe_load((ROOT / ".github" / "workflows" / "azure-deploy.yml").read_text(encoding="utf-8"))
    trigger = deploy[True]
    assert "workflow_dispatch" in trigger
    assert "push" not in trigger
    deploy_text = (ROOT / ".github" / "workflows" / "azure-deploy.yml").read_text(encoding="utf-8")
    assert "Unverified" in deploy_text
    assert "--ingress" not in deploy_text


def test_side_ledger_leaves_the_demo_rows_in_place(tmp_path: Path):
    demo_port = _port()
    side_port = _port()
    demo_state = tmp_path / "demo.sqlite"
    side_state = tmp_path / "side.sqlite"
    corpus = tmp_path / "corpus"
    shutil.copytree(ROOT / "data" / "dev" / "documents", corpus / "documents")
    shutil.copytree(ROOT / "data" / "dev" / "policies", corpus / "policies")
    work = tmp_path / "work"
    submissions = work / "submissions"
    reports = work / "reports"
    demo_log = tmp_path / "demo.log"
    side_log = tmp_path / "side.log"
    pid_path = tmp_path / "side.pid"
    demo = subprocess.Popen(
        [
            sys.executable,
            "tools/runtime.py",
            "serve",
            "--data",
            str(ROOT / "data" / "dev"),
            "--state",
            str(demo_state),
            "--port",
            str(demo_port),
        ],
        cwd=ROOT,
        stdout=demo_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    try:
        _wait(demo_port)
        demo.terminate()
        demo.wait(timeout=5)
        db = sqlite3.connect(demo_state)
        db.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?)",
            ("rev_seed", "CARD-D01", "{}", "abc", "pending", None),
        )
        db.execute("INSERT INTO calls VALUES (?,?,?)", ("CARD-D01", "transactions", 7))
        db.commit()
        db.close()
        demo_log_again = tmp_path / "demo-again.log"
        demo = subprocess.Popen(
            [
                sys.executable,
                "tools/runtime.py",
                "serve",
                "--data",
                str(ROOT / "data" / "dev"),
                "--state",
                str(demo_state),
                "--port",
                str(demo_port),
            ],
            cwd=ROOT,
            stdout=demo_log_again.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
        _wait(demo_port)
        started = subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "start",
                "--data",
                str(ROOT / "data" / "dev"),
                "--state",
                str(side_state),
                "--port",
                str(side_port),
                "--pid",
                str(pid_path),
                "--log",
                str(side_log),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert started.returncode == 0, started.stderr
        assert _healthy(side_port)
        visible = subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "precheck",
                "--fixture",
                str(ROOT / "data" / "dev" / "dataset.json"),
                "--url",
                f"http://127.0.0.1:{demo_port}/health",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert visible.returncode == 1
        assert "fixture is visible" in visible.stderr
        hidden = subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "precheck",
                "--fixture",
                str(corpus / "dataset.json"),
                "--url",
                f"http://127.0.0.1:{demo_port}/health",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert hidden.returncode == 0, hidden.stderr
        assert "fixture is not visible" in hidden.stdout
        passed = subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "run",
                "--data",
                str(corpus),
                "--fixture",
                str(corpus / "dataset.json"),
                "--submissions",
                str(submissions),
                "--reports",
                str(reports),
                "--url",
                f"http://127.0.0.1:{side_port}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert passed.returncode == 0, passed.stderr[-2000:]
        report = tmp_path / "dev.json"
        scored = subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "evaluate",
                "--data",
                str(ROOT / "data" / "dev"),
                "--gold",
                str(ROOT / "feedback" / "dev_gold.json"),
                "--submission",
                str(submissions / "results.json"),
                "--report",
                str(report),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert scored.returncode == 0, scored.stderr[-2000:]
        saved = json.loads(report.read_text(encoding="utf-8"))
        assert saved["passed"] is True
        assert saved["passed_cases"] == 12
        assert saved["total_cases"] == 12
        checks = json.loads((reports / "checklist.json").read_text(encoding="utf-8"))
        assert len(checks) == 12
        assert all(item["passed"] is True for item in checks)
        assert (reports / "metrics.json").is_file()
        assert list((reports / "traces").glob("*.jsonl"))
        demo_db = sqlite3.connect(demo_state)
        reviews = list(demo_db.execute("SELECT id, status FROM reviews"))
        calls = list(demo_db.execute("SELECT case_id, tool, n FROM calls"))
        demo_db.close()
        assert reviews == [("rev_seed", "pending")]
        assert calls == [("CARD-D01", "transactions", 7)]
        side_db = sqlite3.connect(side_state)
        side_reviews = list(side_db.execute("SELECT id FROM reviews"))
        side_calls = list(side_db.execute("SELECT n FROM calls WHERE case_id='CARD-D01' AND tool='transactions'"))
        side_db.close()
        assert side_reviews == []
        assert side_calls and side_calls[0][0] >= 1
    finally:
        if demo.poll() is None:
            demo.terminate()
            try:
                demo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                demo.kill()
        subprocess.run(
            [
                sys.executable,
                "tools/azure_inside.py",
                "cleanup",
                "--pid",
                str(pid_path),
                "--state",
                str(side_state),
                "--path",
                str(side_state),
                "--path",
                str(pid_path),
                "--path",
                str(side_log),
            ],
            cwd=ROOT,
            check=False,
        )
