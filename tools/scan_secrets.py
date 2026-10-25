"""Fail when a tracked file contains a credential-shaped value.

Prints the path and the pattern name. It does not print the matched text.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    ("access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("model-key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("assigned-secret", re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{12,}['\"]")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
SKIP_SUFFIXES = {".sqlite", ".png", ".pdf", ".webp", ".mp4"}


def tracked_files() -> list[str]:
    output = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [line for line in output.splitlines() if line]


def main() -> int:
    hits: list[str] = []
    names = tracked_files()
    if ".env" in names:
        hits.append(".env is tracked")
    for name in names:
        path = ROOT / name
        if not path.is_file() or path.suffix in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{name}: {label}")
    if hits:
        print("\n".join(hits), file=sys.stderr)
        return 1
    print(f"secret scan passed ({len(names)} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
