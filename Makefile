PYTHON ?= python3
VENV_BIN ?= .venv/bin
PYTEST := $(VENV_BIN)/pytest
RUFF := $(VENV_BIN)/ruff
MYPY := $(VENV_BIN)/mypy

.PHONY: install lint typecheck test integration-test frontend-test frontend-build compose-validate load-test verify

install:
	$(PYTHON) -m venv .venv
	$(VENV_BIN)/pip install -e ".[dev,load]"
	cd apps/web && npm ci

lint:
	$(RUFF) check packages apps/api/src apps/scheduler/src apps/node_agent/src tests migrations
	cd apps/web && npm run lint && npm run format:check

typecheck:
	$(MYPY) packages apps/api/src apps/scheduler/src apps/node_agent/src
	cd apps/web && npx vue-tsc -b

test:
	$(PYTEST) tests/unit -q

integration-test:
	$(PYTEST) tests/integration -q

frontend-test:
	cd apps/web && npm run test

frontend-build:
	cd apps/web && npm run build

compose-validate:
	docker compose --env-file .env.example -f deploy/control-plane/compose.yaml config --quiet
	docker compose --env-file .env.example -f deploy/gpu-node/compose.yaml config --quiet

load-test:
	$(VENV_BIN)/locust -f tests/load/locustfile.py --headless -u 100 -r 25 -t 20s --host $${LOAD_TEST_HOST:-http://127.0.0.1:8000}

verify: lint typecheck test integration-test frontend-test frontend-build compose-validate

