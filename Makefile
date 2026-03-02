.PHONY: all setup build test clean lint dev dev-all dev-down

# ─── Top-Level ───────────────────────────────────────────────
NPROC := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

all: build

setup:
	@bash scripts/setup.sh

deps:
	@echo "==> Installing SDK dependencies"
	cd sdk/javascript && npm install
	@echo "==> Setting up Query service"
	cd services/query && uv venv --python 3.12 .venv && uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "==> Setting up Agents service"
	cd services/agents && uv venv --python 3.12 .venv && uv pip install -e ".[dev]" --python .venv/bin/python
	@echo "==> Setting up Pipeline"
	cd pipeline/redis && uv venv --python 3.12 .venv && uv pip install -r requirements.txt --python .venv/bin/python

build: build-sdk build-ingestion build-config

test: test-sdk test-query test-agents test-ingestion test-config

lint: lint-sdk lint-query lint-agents

clean: clean-sdk clean-ingestion clean-config

# ─── SDK ─────────────────────────────────────────────────────

build-sdk:
	cd sdk/javascript && npm run build

test-sdk:
	cd sdk/javascript && npm test

clean-sdk:
	rm -rf sdk/javascript/dist sdk/javascript/node_modules

lint-sdk:
	cd sdk/javascript && npm run lint

# ─── Ingestion Service (C++) ─────────────────────────────────

build-ingestion:
	cd services/ingestion && mkdir -p build && cd build && \
		conan install .. --build=missing --output-folder=. && \
		cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=conan_toolchain.cmake && \
		cmake --build . -j$(NPROC)

test-ingestion:
	cd services/ingestion/build && ctest --output-on-failure

clean-ingestion:
	rm -rf services/ingestion/build

# ─── Config Service (C++) ────────────────────────────────────

build-config:
	cd services/config && mkdir -p build && cd build && \
		conan install .. --build=missing --output-folder=. && \
		cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=conan_toolchain.cmake && \
		cmake --build . -j$(NPROC)

test-config:
	cd services/config/build && ctest --output-on-failure

clean-config:
	rm -rf services/config/build

# ─── Query Service (Python) ──────────────────────────────────

test-query:
	cd services/query && .venv/bin/python -m pytest -v

lint-query:
	cd services/query && .venv/bin/ruff check app/

run-query:
	cd services/query && .venv/bin/uvicorn app.main:app --reload --port 8082

# ─── Agents Service (Python) ─────────────────────────────────

test-agents:
	cd services/agents && .venv/bin/python -m pytest -v

lint-agents:
	cd services/agents && .venv/bin/ruff check app/

run-agents:
	cd services/agents && .venv/bin/uvicorn app.main:app --reload --port 8083

# ─── Pipeline ────────────────────────────────────────────────

run-pipeline:
	cd pipeline/redis && .venv/bin/python clickhouse_writer.py

migrate-clickhouse:
	@echo "==> Running ClickHouse migrations"
	@for f in pipeline/clickhouse/migrations/*.sql; do \
		echo "  Applying $$f"; \
		clickhouse-client --multiquery < "$$f"; \
	done

# ─── Docker ──────────────────────────────────────────────────

dev:
	docker compose -f infra/docker/docker-compose.deps.yml up -d
	@echo "==> Dependencies running (Redis, ClickHouse, PostgreSQL)"
	@echo "    Run services individually: make run-query, make run-agents, make run-pipeline"

dev-all:
	docker compose -f infra/docker/docker-compose.yml up --build

dev-down:
	docker compose -f infra/docker/docker-compose.yml down
	docker compose -f infra/docker/docker-compose.deps.yml down

# ─── CI ──────────────────────────────────────────────────────

ci: lint-sdk test-sdk lint-query lint-agents
