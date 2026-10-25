"""Process settings. Defaults point at the local mock and the dev corpus."""

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROCEDURE_ID = "CARD-PROCEDURE-01"


@dataclass(frozen=True)
class Settings:
    mock_base_url: str
    data_dir: Path
    submissions_dir: Path
    procedure_id: str

    @classmethod
    def from_sources(
        cls,
        mock_url: str | None,
        data_dir: Path | None,
        submissions: Path | None,
        procedure_id: str | None = None,
    ) -> "Settings":
        base = (mock_url or os.environ.get("MOCK_BASE_URL") or "http://127.0.0.1:8765").rstrip("/")
        data = data_dir or _env_path("DATA_DIR") or Path.cwd() / "data" / "dev"
        folder = submissions or _env_path("SUBMISSIONS_DIR") or Path.cwd() / "submissions"
        procedure = procedure_id or os.environ.get("PROCEDURE_ID") or DEFAULT_PROCEDURE_ID
        return cls(base, data, folder, procedure)


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw)
