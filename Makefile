.PHONY: sync lint test check daily mock regen-gold

sync:
	uv sync --locked

lint:
	uv run --locked python tools/lint_gates.py

test:
	uv run --locked pytest

check: lint test

daily: check
	@echo "Checkpoint 1 drafts a denied authorization only. CARD-D01 and CARD-D11 are scored here. 12/12 is the next freeze."

mock:
	uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite

regen-gold:
	@echo "refusing: hidden-style gold is not in this checkpoint, and feedback/dev_gold.json is never rewritten" >&2
	@exit 2
