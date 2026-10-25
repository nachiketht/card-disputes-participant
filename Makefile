.PHONY: sync lint test check daily mock regen-gold metrics week10 week10-duplicate

sync:
	uv sync --locked

lint:
	uv run --locked python tools/lint_gates.py

test:
	uv run --locked pytest

check: lint test

daily: check
	@echo "The rules engine drafts every development case. Score the written results with tools/evaluate.py."

mock:
	uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite

regen-gold:
	@echo "refusing: hidden-style gold is not in this checkpoint, and feedback/dev_gold.json is never rewritten" >&2
	@exit 2

metrics:
	uv run --locked dispute-copilot metrics

week10:
	scripts/week10_evidence.sh

week10-duplicate:
	scripts/week10_duplicate.sh
