"""Fail when the stdio server registers an approve, reject, or reset call.

Also fails if that module names the mock fixture file.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "dispute_copilot" / "mcp_server.py"
BANNED = ("approve", "reject", "reset")


def registered_names(text: str) -> list[str]:
    tree = ast.parse(text)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_tool":
            continue
        if not node.args or not isinstance(node.args[0], ast.Name):
            continue
        found.append(node.args[0].id)
    return found


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8")
    problems: list[str] = []
    if "dataset.json" in text:
        problems.append("mcp_server.py names the mock fixture file")
    names = registered_names(text)
    if not names:
        problems.append("mcp_server.py registers no calls")
    for name in names:
        compact = name.lower().replace("_", "").replace("-", "")
        for banned in BANNED:
            if banned in compact:
                problems.append(f"registered call {name} contains {banned}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("stdio server call list: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
