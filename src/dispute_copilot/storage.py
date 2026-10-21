"""Atomic draft versions and a locked upsert into the submission array.

The evaluator rejects a duplicate case id in the submission file, and it rejects
the whole file for that. Replacing by case id keeps a rerun from doing that.
The lock covers version allocation and the array update together.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from dispute_copilot.fields import CASE_ID


@contextmanager
def submission_lock(submissions: Path):
    submissions.mkdir(parents=True, exist_ok=True)
    handle = open(submissions / ".write.lock", "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _version_number(path: Path) -> int | None:
    if path.suffix != ".json" or len(path.stem) < 2 or path.stem[0] != "v":
        return None
    if not path.stem[1:].isdigit():
        return None
    return int(path.stem[1:])


def latest_draft(submissions: Path, case_id: str) -> tuple[Path, dict, int]:
    case_dir = submissions / "drafts" / case_id
    numbers = []
    if case_dir.is_dir():
        for path in case_dir.glob("v*.json"):
            number = _version_number(path)
            if number is not None:
                numbers.append(number)
    if not numbers:
        raise FileNotFoundError(f"no draft has been written for {case_id}")
    version = max(numbers)
    path = case_dir / f"v{version}.json"
    draft = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(draft, dict):
        raise ValueError(f"draft {path.name} is not an object")
    return path, draft, version


def write_draft(submissions: Path, case_id: str, draft: dict) -> Path:
    text = json.dumps(draft, indent=2, ensure_ascii=False) + "\n"
    with submission_lock(submissions):
        case_dir = submissions / "drafts" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        numbers = [number for path in case_dir.glob("v*.json") if (number := _version_number(path)) is not None]
        version = max(numbers, default=0) + 1
        path = case_dir / f"v{version}.json"
        atomic_write(path, text)
        _upsert_unlocked(submissions / "results.json", draft)
    return path


def upsert_results(path: Path, draft: dict) -> None:
    with submission_lock(path.parent):
        _upsert_unlocked(path, draft)


def _upsert_unlocked(path: Path, draft: dict) -> None:
    current: list = []
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("results file must be a JSON array")
        current = loaded
    case_id = draft[CASE_ID]
    replaced = False
    merged = []
    for item in current:
        if isinstance(item, dict) and item.get(CASE_ID) == case_id:
            if not replaced:
                merged.append(draft)
                replaced = True
            continue
        merged.append(item)
    if not replaced:
        merged.append(draft)
    atomic_write(path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
