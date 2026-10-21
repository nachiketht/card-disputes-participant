"""Process settings. Defaults point at the local mock and the dev corpus."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    mock_base_url: str
    data_dir: Path
    submissions_dir: Path

    @classmethod
    def from_sources(cls, mock_url: str | None, data_dir: Path | None, submissions: Path | None) -> "Settings":
        base = (mock_url or os.environ.get("MOCK_BASE_URL") or "http://127.0.0.1:8765").rstrip("/")
        data = data_dir or _env_path("DATA_DIR") or Path.cwd() / "data" / "dev"
        folder = submissions or _env_path("SUBMISSIONS_DIR") or Path.cwd() / "submissions"
        return cls(base, data, folder)


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw)
