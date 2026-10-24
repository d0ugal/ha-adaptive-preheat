RUFF_VERSION ?= 0.15.9
RUFF = docker run --rm -v "$(PWD)":/src -w /src ghcr.io/astral-sh/ruff:$(RUFF_VERSION)
UV_RUN = uv run --managed-python --python 3.13

.PHONY: lint format test simulate

lint:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) check --fix .
	$(RUFF) format .

test:
	$(UV_RUN) pytest -q

simulate:
	$(UV_RUN) python scripts/simulate.py --days 14
