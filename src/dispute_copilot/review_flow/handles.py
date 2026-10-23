"""Read and write the per-case review handle."""

import json
from pathlib import Path


def handle_path(submissions: Path, case_id: str) -> Path:
    return submissions / "reviews" / f"{case_id}.json"


def read_handle(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"review handle {path.name} is not an object")
    return loaded


def write_handle(path: Path, handle: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(handle, indent=2) + "\n", encoding="utf-8")
