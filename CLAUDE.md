# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is APDL?

Autonomous Product Development Loop — a self-optimizing product analytics and experimentation platform. It ingests user behavior events, runs analytics queries, evaluates feature flags and A/B experiments, and uses LLM-powered agents to autonomously generate insights, design experiments, and personalize user experiences.

## Build & Development Commands

```bash
make setup              # Full local dev setup (uv venvs, npm install, Docker deps, migrations, .env)
make build              # Build SDK + C++ services
make test               # Run all tests
make lint               # Run all linters
make dev                # Start Docker deps only (Redis, ClickHouse, PostgreSQL)
make dev-all            # Start full stack via Docker Compose
make dev-down           # Stop all containers
make migrate-clickhouse # Apply ClickHouse SQL migrations
```

### Running individual services (with hot-reload)

```bash
make run-query          # Query Service → localhost:8082
make run-agents         # Agents Service → localhost:8083
make run-pipeline       # ClickHouse Writer (Redis Streams consumer)
```

### Per-service build/test/lint

| Service | Build | Test | Lint |
|---------|-------|------|------|
| SDK | `make build-sdk` | `make test-sdk` | `make lint-sdk` |
| Ingestion (C++) | `make build-ingestion` | `make test-ingestion` | — |
| Config (C++) | `make build-config` | `make test-config` | — |
| Query (Python) | — | `make test-query` | `make lint-query` |
| Agents (Python) | — | `make test-agents` | `make lint-agents` |

### Running a single test

```bash
# SDK (Vitest)
cd sdk/javascript && npm test -- core/client.test.ts

# Python services (pytest)
cd services/query && .venv/bin/python -m pytest tests/test_funnels.py -v
cd services/agents && .venv/bin/python -m pytest tests/test_supervisor.py::test_specific -v

# C++ services (CTest) — must build first
cd services/ingestion/build && ctest -R test_events_handler --output-on-failure -V
cd services/config/build && ctest -R test_evaluator --output-on-failure -V
```

## Architecture Overview

The system is a polyglot monorepo with four services, a data pipeline, and a client SDK:

```
SDK (TypeScript) ──POST /v1/events──→ Ingestion (C++/Crow :8080) ──→ Redis Streams
                 ←─SSE /v1/stream──── Config (C++/Crow :8081) ←───→ PostgreSQL + Redis Cache
                                              ↑
Redis Streams ──→ ClickHouse Writer (Python) ──→ ClickHouse
                                                      ↓
                                              Query Service (Python/FastAPI :8082)
                                                      ↓
                                              Agents Service (Python/FastAPI+LangGraph :8083)
                                              ↕ PostgreSQL (pgvector) for memory
```

### Data Flow

1. **Event ingestion:** SDK → Ingestion Service (auth, rate-limit, schema validation) → Redis Streams (`events:raw:{project_id}`)
2. **Event pipeline:** ClickHouse Writer consumes Redis Streams in batches (1000 events or 5s flush) → ClickHouse (MergeTree tables, materialized views)
3. **Flag distribution:** Config Service stores flags/experiments in PostgreSQL, caches in Redis, pushes updates via SSE to SDK
4. **Flag evaluation:** SDK evaluates flags client-side using MurmurHash3 bucketing (no server round-trip for evaluation)
5. **Analytics:** Query Service queries ClickHouse for funnels, cohorts, retention, experiment stats (frequentist/Bayesian/sequential)
6. **Autonomous agents:** LangGraph state machines orchestrate LLM-driven workflows — behavior analysis, experiment design, personalization, feature proposals. Actions pass through safety validation with audit logging and rollback support

### Tech Stack by Service

- **SDK** (`sdk/javascript/`): TypeScript, Rollup (ESM/CJS/IIFE), Vitest (jsdom)
- **Ingestion** (`services/ingestion/`): C++17, Crow, hiredis, RapidJSON, spdlog — CMake + Conan, GTest
- **Config** (`services/config/`): C++17, Crow, hiredis, libpq, RapidJSON, spdlog — CMake + Conan, GTest
- **Query** (`services/query/`): Python 3.12, FastAPI, clickhouse-driver/asynch, SciPy, NumPy — uv, pytest-asyncio, ruff
- **Agents** (`services/agents/`): Python 3.12, FastAPI, LangGraph, LiteLLM, asyncpg, pgvector — uv, pytest-asyncio, ruff
- **Pipeline** (`pipeline/redis/`): Python 3.12, redis async client, clickhouse-driver

### Key Ports

| Service | Port |
|---------|------|
| Ingestion | 8080 |
| Config | 8081 |
| Query | 8082 |
| Agents | 8083 |
| Redis | 6379 |
| ClickHouse HTTP / Native | 8123 / 9000 |
| PostgreSQL | 5432 |

## Tooling & Conventions

- **Python package management:** `uv` (not pip directly). Each Python service has its own `.venv/` directory
- **C++ build:** Conan 2.x for deps → CMake with `conan_toolchain.cmake`
- **Python linting:** `ruff check app/` (default config, no pyproject.toml overrides)
- **SDK linting:** `tsc --noEmit` (strict mode, no unused locals/params)
- **SDK test pattern:** `__tests__/**/*.test.ts`
- **Python test pattern:** `tests/` directory in each service
- **CI runs on push/PR to main:** SDK tests + build, Python linting (ruff), C++ build + test
- **Releases:** git tags matching `v*` trigger npm publish + Docker image builds to GHCR

## Environment Variables

Infrastructure defaults for local dev (set via `make setup` from `.env.example`):

```
REDIS_URL=redis://localhost:6379
POSTGRES_URL=postgresql://apdl:apdl_dev@localhost:5432/apdl
CLICKHOUSE_URL=http://localhost:8123 (HTTP) / clickhouse://apdl:apdl_dev@localhost:9000/apdl (native)
```

Agents service requires `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` for LLM access.
