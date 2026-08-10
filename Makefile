PYTHON ?= python3
VENV_BIN ?= .venv/bin
PYTEST := $(VENV_BIN)/pytest
RUFF := $(VENV_BIN)/ruff
MYPY := $(VENV_BIN)/mypy

.PHONY: install lint typecheck test integration-test frontend-test frontend-build compose-validate load-test load-test-execute release-package-plan release-package-execute verify verify-release-identity

install:
	$(PYTHON) -m venv .venv
	$(VENV_BIN)/pip install -e ".[dev,load]"
	cd apps/web && npm ci

lint:
	$(RUFF) check packages apps scripts tests migrations
	cd apps/web && npm run lint && npm run format:check

typecheck:
	$(MYPY) packages apps/api/src apps/scheduler/src apps/node_agent/src apps/asset_api/src apps/blender_worker/src
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
	docker compose --env-file .env.node.example -f deploy/gpu-node/compose.yaml config --quiet

load-test:
	$(VENV_BIN)/python scripts/run_six_api_load.py \
		--scenario "$${LOAD_TEST_SCENARIO_FILE:-tests/load/scenarios/six_api_120.example.yaml}" \
		--fixtures "$${LOAD_TEST_FIXTURE_MANIFEST:-tests/load/fixtures/six_api.example.yaml}"

load-test-execute:
	$(VENV_BIN)/python scripts/run_six_api_load.py \
		--scenario "$${LOAD_TEST_SCENARIO_FILE:-tests/load/scenarios/six_api_120.example.yaml}" \
		--fixtures "$${LOAD_TEST_FIXTURE_MANIFEST:-tests/load/fixtures/six_api.example.yaml}" \
		--execute

release-package-plan:
	@: "$${RELEASE_VERSION:?set RELEASE_VERSION}"
	@: "$${RELEASE_REVISION:?set RELEASE_REVISION to the pushed 40-character source SHA}"
	$(PYTHON) scripts/package_control_plane_release.py \
		--version "$${RELEASE_VERSION}" \
		--worker-version "$${RELEASE_WORKER_VERSION:-1.4.7-retopology-coordinate-restore-v2}" \
		--revision "$${RELEASE_REVISION}"

release-package-execute:
	@: "$${RELEASE_VERSION:?set RELEASE_VERSION}"
	@: "$${RELEASE_REVISION:?set RELEASE_REVISION to the pushed 40-character source SHA}"
	@: "$${RELEASE_SBOM_GENERATOR:?set RELEASE_SBOM_GENERATOR to name@sha256}"
	@: "$${RELEASE_PACKAGE_CONFIRM:?copy the exact confirmation token from release-package-plan}"
	$(PYTHON) scripts/package_control_plane_release.py \
		--version "$${RELEASE_VERSION}" \
		--worker-version "$${RELEASE_WORKER_VERSION:-1.4.7-retopology-coordinate-restore-v2}" \
		--revision "$${RELEASE_REVISION}" \
		--sbom-generator "$${RELEASE_SBOM_GENERATOR}" \
		--execute \
		--confirm "$${RELEASE_PACKAGE_CONFIRM}"

verify: lint typecheck test integration-test frontend-test frontend-build compose-validate

verify-release-identity:
	@: "$${RELEASE_VERSION:?set RELEASE_VERSION}"
	@: "$${RELEASE_REVISION:?set RELEASE_REVISION to the committed 40-character Git SHA}"
	@: "$${RELEASE_API_IMAGE:?set RELEASE_API_IMAGE to a pushed immutable image reference}"
	@: "$${RELEASE_SCHEDULER_IMAGE:?set RELEASE_SCHEDULER_IMAGE}"
	@: "$${RELEASE_ASSET_API_IMAGE:?set RELEASE_ASSET_API_IMAGE}"
	@: "$${RELEASE_WEB_IMAGE:?set RELEASE_WEB_IMAGE}"
	@: "$${RELEASE_BLENDER_WORKER_IMAGE:?set RELEASE_BLENDER_WORKER_IMAGE}"
	@: "$${RELEASE_SBOM_DIR:?set RELEASE_SBOM_DIR}"
	$(PYTHON) scripts/verify_release_identity.py \
		--expected-version "$${RELEASE_VERSION}" \
		--expected-worker-version "$${RELEASE_WORKER_VERSION:-1.4.7-retopology-coordinate-restore-v2}" \
		--expected-revision "$${RELEASE_REVISION}" \
		--remote-ref "$${RELEASE_REMOTE_REF:-origin/main}" \
		--image "api=$${RELEASE_API_IMAGE}" \
		--image "scheduler=$${RELEASE_SCHEDULER_IMAGE}" \
		--image "asset-api=$${RELEASE_ASSET_API_IMAGE}" \
		--image "web=$${RELEASE_WEB_IMAGE}" \
		--image "blender-worker=$${RELEASE_BLENDER_WORKER_IMAGE}" \
		--sbom "api=$${RELEASE_SBOM_DIR}/api.intoto.json" \
		--sbom "scheduler=$${RELEASE_SBOM_DIR}/scheduler.intoto.json" \
		--sbom "asset-api=$${RELEASE_SBOM_DIR}/asset-api.intoto.json" \
		--sbom "web=$${RELEASE_SBOM_DIR}/web.intoto.json" \
		--sbom "blender-worker=$${RELEASE_SBOM_DIR}/blender-worker.intoto.json" \
		--output "$${RELEASE_SBOM_DIR}/release-identity.json"
