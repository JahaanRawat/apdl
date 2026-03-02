.PHONY: all build test clean dev deps

# ─── Top-Level ───────────────────────────────────────────────

all: build

deps:
	@echo "==> Installing SDK dependencies"
	cd sdk/javascript && npm install
	@echo "==> Installing Query service dependencies"
	cd services/query && pip install -e ".[dev]"
	@echo "==> Installing Agents service dependencies"
	cd services/agents && pip install -e ".[dev]"

build: build-sdk build-ingestion build-config

test: test-sdk test-query test-agents test-ingestion test-config

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
		conan install .. --build=missing && \
		cmake .. -DCMAKE_BUILD_TYPE=Release && \
		cmake --build . -j$$(nproc 2>/dev/null || sysctl -n hw.ncpu)

test-ingestion:
	cd services/ingestion/build && ctest --output-on-failure

clean-ingestion:
	rm -rf services/ingestion/build

# ─── Config Service (C++) ────────────────────────────────────

build-config:
	cd services/config && mkdir -p build && cd build && \
		conan install .. --build=missing && \
		cmake .. -DCMAKE_BUILD_TYPE=Release && \
		cmake --build . -j$$(nproc 2>/dev/null || sysctl -n hw.ncpu)

test-config:
	cd services/config/build && ctest --output-on-failure

clean-config:
	rm -rf services/config/build

# ─── Query Service (Python) ──────────────────────────────────

test-query:
	cd services/query && python -m pytest -v

lint-query:
	cd services/query && ruff check app/

# ─── Agents Service (Python) ─────────────────────────────────

test-agents:
	cd services/agents && python -m pytest -v

lint-agents:
	cd services/agents && ruff check app/

# ─── Pipeline ────────────────────────────────────────────────

migrate-clickhouse:
	@echo "==> Running ClickHouse migrations"
	@for f in pipeline/clickhouse/migrations/*.sql; do \
		echo "  Applying $$f"; \
		clickhouse-client --multiquery < "$$f"; \
	done

# ─── Docker ──────────────────────────────────────────────────

dev:
	docker compose -f infra/docker/docker-compose.deps.yml up -d
	@echo "==> Dependencies running. Start services individually or use:"
	@echo "    docker compose -f infra/docker/docker-compose.yml up"

dev-all:
	docker compose -f infra/docker/docker-compose.yml up --build

dev-down:
	docker compose -f infra/docker/docker-compose.yml down
	docker compose -f infra/docker/docker-compose.deps.yml down

# ─── CI ──────────────────────────────────────────────────────

ci: lint-sdk test-sdk test-query test-agents
