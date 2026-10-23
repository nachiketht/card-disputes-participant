"""Versioned prompts. The active file is pinned in config, and the variable set is closed.

A raw complaint, attachment, or merchant payload is not a variable. The
template fallback does not go through this registry.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from dispute_copilot.fields import (
    CLASSIFICATION,
    DEADLINE,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    ROUTING,
)

_RAW_PROSE = frozenset(
    {
        "complaint",
        "attachment",
        "document",
        "intake",
        "followup",
        "merchant_payload",
        "prose",
        "source_text",
    }
)
_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    prompt_version: int
    prompt_hash: str
    system: str
    user: str


class PromptRegistry:
    def __init__(self, root: Path | None = None, rules_path: Path | None = None) -> None:
        self.root = root or (_ROOT / "prompts")
        self.rules_path = rules_path or (_ROOT / "config" / "rules" / "prompts.yaml")

    def render(self, task: str, variables: dict[str, str]) -> RenderedPrompt:
        self._reject_raw(variables)
        version = self._pinned_version(task)
        path = self.root / task / f"v{version}.md"
        if not path.is_file():
            raise ValueError(f"pinned prompt {task} v{version} is not on disk")
        raw = path.read_text(encoding="utf-8")
        meta, body = _split_front_matter(raw)
        if meta.get("task") != task:
            raise ValueError(f"prompt task does not match {task}")
        if int(meta.get("version", -1)) != version:
            raise ValueError(f"prompt version does not match the pin for {task}")
        declared = meta.get("variables")
        if not isinstance(declared, list) or not declared:
            raise ValueError(f"prompt {task} does not declare variables")
        expected = {str(item) for item in declared}
        provided = set(variables)
        if provided != expected:
            raise ValueError(f"prompt variables for {task} must match the declared set")
        system = body
        for name, value in variables.items():
            system = system.replace("{{" + name + "}}", value)
        if "{{" in system:
            raise ValueError(f"prompt {task} has an unsubstituted placeholder")
        prompt_id = f"{meta.get('id', task)}.v{version}"
        return RenderedPrompt(
            prompt_id=prompt_id,
            prompt_version=version,
            prompt_hash=hashlib.sha256(raw.encode()).hexdigest(),
            system=system.strip(),
            user="Write the customer message now. Use only the facts in the instructions.",
        )

    def _pinned_version(self, task: str) -> int:
        if not self.rules_path.is_file():
            raise ValueError("prompt pin file is missing")
        loaded = yaml.safe_load(self.rules_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or task not in loaded:
            raise ValueError(f"no pinned version for {task}")
        version = loaded[task]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"pinned version for {task} is not a positive integer")
        return version

    def _reject_raw(self, variables: dict[str, str]) -> None:
        banned = set(variables) & _RAW_PROSE
        if banned:
            raise ValueError("raw prose is not a prompt variable")
        for value in variables.values():
            if not isinstance(value, str):
                raise ValueError("prompt variables must be strings")
            if "Source ID:" in value:
                raise ValueError("raw document text is not a prompt variable")


def allowed_variable_names() -> frozenset[str]:
    """Names the drafter may pass. Kept here so call sites use the constants."""
    return frozenset(
        {
            CLASSIFICATION,
            ROUTING,
            FACT_TRANSACTION_ID,
            FACT_REPORTED_DATE,
            DEADLINE,
            "policy_id",
            "missing_fields",
            "transaction_attempts",
            "quotes",
        }
    )


def _split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("prompt file has no front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("prompt front matter does not close")
    meta = yaml.safe_load(text[4:end])
    if not isinstance(meta, dict):
        raise ValueError("prompt front matter is not a mapping")
    return meta, text[end + 5 :]
