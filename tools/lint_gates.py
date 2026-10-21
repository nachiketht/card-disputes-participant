"""Source gates shared by the pre-commit hook and CI.

Contract names may be spelled only in fields.py. An approve callable, a read of
the mock fixture file, a committed state file, and a restricted token in source
all fail closed.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dispute_copilot.fields import contract_literals  # noqa: E402


def string_literals(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.value))
    return found


def approve_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    lines = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name == "approve":
                lines.append(node.lineno)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == "approve":
                lines.append(node.lineno)
            if isinstance(func, ast.Attribute) and func.attr == "approve":
                lines.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    return lines


def scan_tree() -> list[str]:
    problems = []
    banned = contract_literals()
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        if "dataset.json" in text:
            problems.append(f"{relative} mentions the mock fixture file")
        if "SYNTHETIC-PRIVATE" in text or "CUST-" in text or "private_token" in text:
            problems.append(f"{relative} contains a restricted marker")
        for line in approve_calls(text):
            problems.append(f"{relative}:{line} defines or calls approve")
        if path.name == "fields.py":
            continue
        for line, value in string_literals(text):
            if value in banned:
                problems.append(f"{relative}:{line} spells a contract name ({value})")
    return problems


def staged_state_files() -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "diff", "--cached", "--diff-filter=ACMRTUXB", "--name-only"],
            cwd=ROOT,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ["could not read the git index; refusing to skip the state-file gate"]
    problems = []
    for path in output.splitlines():
        if path == ".state" or path.startswith(".state/"):
            problems.append(f"staged path is under .state/: {path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    problems = scan_tree()
    if "--staged" in argv:
        problems.extend(staged_state_files())
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"lint gates passed ({len(list(SRC.rglob('*.py')))} source files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
