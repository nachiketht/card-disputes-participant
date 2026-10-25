"""JSON-lines logging. Each trace record is also one log line."""

import json
import logging
import sys
from pathlib import Path

from dispute_copilot.observability.tracing import LOGGER, safe


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = getattr(record, "record", None)
        if isinstance(body, dict):
            line = {"level": record.levelname.lower(), **body}
        else:
            line = {"level": record.levelname.lower(), "name": "log", "message": safe(record.getMessage())}
        return json.dumps(line, ensure_ascii=False, sort_keys=True)


def configure(log_file: Path | None, to_stderr: bool = False) -> None:
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    formatter = JsonFormatter()
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)
    if to_stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        LOGGER.addHandler(stream)
    if not LOGGER.handlers:
        LOGGER.addHandler(logging.NullHandler())
