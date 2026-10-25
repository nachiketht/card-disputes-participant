"""Drive the running Azure container from a push. Do not change the revision.

Sign-in uses federated credentials already present in the process. This file
does not read or print secret values. A missing sign-in skips the live
container. A fork skips it as well.
"""

import argparse
import base64
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

APP = "card-disputes"
GROUP = "card-disputes-rg"
SIDE_PORT = 8766
LIVE_HEALTH = "http://127.0.0.1:8765/health"
INSIDE = Path(__file__).with_name("azure_inside.py")
RESULT = "AZURE_CI_RESULT"
BUNDLE_BEGIN = "AZURE_CI_BUNDLE_BEGIN"
BUNDLE_END = "AZURE_CI_BUNDLE_END"


def safe_id(raw: str) -> str:
    cleaned = "".join(ch for ch in raw if ch.isalnum())
    return (cleaned or "run")[:24]


def paths(run_id: str) -> dict[str, str]:
    root = f"/tmp/ci{safe_id(run_id)}"
    return {
        "state": f"{root}.sqlite",
        "pid": f"{root}.pid",
        "log": f"{root}.log",
        "submissions": f"{root}/submissions",
        "reports": f"{root}/reports",
        "gold": f"{root}.gold.json",
        "results": f"{root}.results.json",
        "report": f"{root}.dev.json",
        "script": f"{root}.py",
        "script_b64": f"{root}.b64",
    }


def mode_commands(run_id: str) -> list[tuple[str, str]]:
    """Container and command. The run talks to the side port, not the demo ledger."""
    item = paths(run_id)
    script = item["script"]
    side = f"http://127.0.0.1:{SIDE_PORT}"
    return [
        ("app", f"cd /app && python {script} precheck --fixture /data/dataset.json --url {LIVE_HEALTH}"),
        (
            "mock",
            "cd /app && python {script} start --data /data --state {state} --port {port} --pid {pid} --log {log}".format(
                script=script,
                state=item["state"],
                port=SIDE_PORT,
                pid=item["pid"],
                log=item["log"],
            ),
        ),
        (
            "app",
            "cd /app && python {script} run --data /data --fixture /data/dataset.json "
            "--submissions {submissions} --reports {reports} --url {side}".format(
                script=script,
                submissions=item["submissions"],
                reports=item["reports"],
                side=side,
            ),
        ),
        (
            "mock",
            "cd /app && python {script} evaluate --data /data --gold {gold} --submission {results} --report {report}".format(
                script=script,
                gold=item["gold"],
                results=item["results"],
                report=item["report"],
            ),
        ),
    ]


def cleanup_commands(run_id: str) -> list[tuple[str, str]]:
    item = paths(run_id)
    script = item["script"]
    root = item["submissions"].rsplit("/", 1)[0]
    return [
        (
            "mock",
            "cd /app && python {script} cleanup --pid {pid} --state {state} --path {state} --path {pid} "
            "--path {log} --path {gold} --path {results} --path {report} --path {script} --path {b64} "
            "--path {gold}.b64 --path {results}.b64".format(
                script=script,
                pid=item["pid"],
                state=item["state"],
                log=item["log"],
                gold=item["gold"],
                results=item["results"],
                report=item["report"],
                b64=item["script_b64"],
            ),
        ),
        (
            "app",
            "cd /app && python {script} cleanup --path {submissions} --path {reports} --path {root} "
            "--path {script} --path {b64}".format(
                script=script,
                submissions=item["submissions"],
                reports=item["reports"],
                root=root,
                b64=item["script_b64"],
            ),
        ),
    ]


def upload_commands(script_path: str, b64_path: str, payload: bytes) -> list[str]:
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = [encoded[index : index + 900] for index in range(0, len(encoded), 900)] or [""]
    commands = [f"python -c \"open('{b64_path}','w').close()\""]
    for chunk in chunks:
        commands.append(f"python -c \"open('{b64_path}','a').write('{chunk}')\"")
    commands.append(
        "python -c \"import base64,pathlib; "
        f"pathlib.Path('{script_path}').write_bytes(base64.b64decode(pathlib.Path('{b64_path}').read_text()))\""
    )
    return commands


def gate() -> int:
    if os.environ.get("AZURE_FORK", "").strip().lower() == "true":
        print("This run is from a fork. The live container was not contacted.", file=sys.stderr)
        print("run=skip")
        return 0
    client = os.environ.get("AZURE_CLIENT_ID", "").strip() or os.environ.get("AZURE_CLIENT_ID_VAR", "").strip()
    tenant = os.environ.get("AZURE_TENANT_ID", "").strip() or os.environ.get("AZURE_TENANT_ID_VAR", "").strip()
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip() or os.environ.get("AZURE_SUBSCRIPTION_ID_VAR", "").strip()
    if not client or not tenant or not subscription:
        print(
            "Azure sign-in is not configured. The live container was not contacted. "
            "Set AZURE_CLIENT_ID, AZURE_TENANT_ID, and AZURE_SUBSCRIPTION_ID.",
            file=sys.stderr,
        )
        print("run=skip")
        return 0
    print("run=yes")
    return 0


def result_code(output: str) -> int | None:
    found = None
    for line in output.splitlines():
        text = line.strip().replace("\r", "")
        prefix = RESULT + " code="
        if text.startswith(prefix):
            try:
                found = int(text[len(prefix) :])
            except ValueError:
                found = None
    return found


def take_bundle(output: str) -> bytes | None:
    if BUNDLE_BEGIN not in output or BUNDLE_END not in output:
        return None
    raw = output.split(BUNDLE_BEGIN, 1)[1].split(BUNDLE_END, 1)[0]
    raw = "".join(raw.split())
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except ValueError:
        return None


def safe_line(line: str) -> str:
    lowered = line.lower()
    if "llm_api_key" in lowered or "llm-api-key" in lowered:
        return "[line omitted]"
    if len(line) > 200:
        return "[long line omitted]"
    return line


def show_tail(output: str) -> None:
    lines = [safe_line(line) for line in output.splitlines() if line.strip()]
    print("\n".join(lines[-30:]), file=sys.stderr)


def az_exec(container: str, command: str) -> tuple[int, str]:
    inner = shlex.join(
        [
            "az",
            "containerapp",
            "exec",
            "--name",
            APP,
            "--resource-group",
            GROUP,
            "--container",
            container,
            "--command",
            command,
        ]
    )
    proc = subprocess.run(
        ["script", "--return", "-q", "-c", inner, "/dev/null"],
        text=True,
        capture_output=True,
        timeout=600,
    )
    return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")


def _call(call, container: str, command: str, require_marker: bool = True) -> tuple[int | None, str]:
    print(f"exec container={container}", file=sys.stderr)
    try:
        _code, output = call(container, command)
    except (OSError, subprocess.TimeoutExpired):
        print("exec failed", file=sys.stderr)
        return None, ""
    if not require_marker:
        return 0, output
    marker = result_code(output)
    if marker is None:
        print("the container did not return a result", file=sys.stderr)
        show_tail(output)
    return marker, output


def _extract(bundle: bytes, artifact: Path) -> None:
    import io

    artifact.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        archive.extractall(artifact, filter="data")


def execute(artifact: Path, call=az_exec, run_id: str | None = None, script: bytes | None = None, gold: bytes | None = None) -> int:
    run_id = safe_id(run_id or (os.environ.get("GITHUB_RUN_ID", "local") + os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
    item = paths(run_id)
    script = script if script is not None else INSIDE.read_bytes()
    gold = gold if gold is not None else (Path.cwd() / "feedback" / "dev_gold.json").read_bytes()
    failed = False
    try:
        for container in ("app", "mock"):
            for command in upload_commands(item["script"], item["script_b64"], script):
                marker, _output = _call(call, container, f"cd /app && {command}", require_marker=False)
                if marker is None:
                    print("the live container is unreachable", file=sys.stderr)
                    return 1
        marker, output = _call(call, "app", mode_commands(run_id)[0][1])
        if marker is None:
            print("the live container is unreachable", file=sys.stderr)
            return 1
        if marker != 0:
            show_tail(output)
            return 1
        marker, output = _call(call, "mock", mode_commands(run_id)[1][1])
        if marker != 0:
            print("the side mock did not stay up on 127.0.0.1. The demo ledger was not used.", file=sys.stderr)
            show_tail(output)
            return 1
        marker, output = _call(call, "app", mode_commands(run_id)[2][1])
        bundle = take_bundle(output)
        if bundle:
            _extract(bundle, artifact)
            print(f"received bundle bytes={len(bundle)}", file=sys.stderr)
        if marker != 0:
            failed = True
            show_tail(output)
        results = artifact / "results.json"
        if results.is_file():
            for command in upload_commands(item["results"], item["results"] + ".b64", results.read_bytes()):
                sent, _output = _call(call, "mock", f"cd /app && {command}", require_marker=False)
                if sent is None:
                    failed = True
        for command in upload_commands(item["gold"], item["gold"] + ".b64", gold):
            sent, _output = _call(call, "mock", f"cd /app && {command}", require_marker=False)
            if sent is None:
                failed = True
        marker, output = _call(call, "mock", mode_commands(run_id)[3][1])
        report = take_bundle(output)
        if report:
            (artifact / "dev.json").write_bytes(report)
            print(f"received evaluation bytes={len(report)}", file=sys.stderr)
        if marker != 0:
            failed = True
            if marker is None:
                print("the evaluator did not return a result", file=sys.stderr)
            show_tail(output)
        return 1 if failed else 0
    finally:
        unfinished = []
        for container, command in cleanup_commands(run_id):
            marker, output = _call(call, container, command)
            if marker != 0:
                unfinished.append(container)
                show_tail(output)
        if unfinished:
            print(
                "cleanup did not finish. The demo ledger /state/run.sqlite was not opened. "
                "Drafts under /submissions were not deleted. A side process may still be "
                "listening on 127.0.0.1 with a state file under /tmp. This job does not "
                "stop a process it cannot identify.",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gate")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "gate":
        return gate()
    if args.command == "plan":
        run_id = safe_id(os.environ.get("GITHUB_RUN_ID", "local") + os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
        for container, command in mode_commands(run_id) + cleanup_commands(run_id):
            print(f"{container}: {command}")
        return 0
    return execute(args.artifact)


if __name__ == "__main__":
    sys.exit(main())
