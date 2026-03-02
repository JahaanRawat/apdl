# APDL

**Autonomous Product Development Loop** — a self-optimizing product analytics and experimentation platform. APDL ingests user behavior events, runs analytics queries, evaluates feature flags and A/B experiments, and uses LLM-powered agents to autonomously generate insights, design experiments, and personalize user experiences.

## Architecture

<p align="center">
  <img src="docs/architecture.svg" alt="APDL Architecture" width="900"/>
</p>

## Project Structure

```
apdl/
├── sdk/javascript/          # @apdl/sdk — TypeScript client SDK
│   ├── src/
│   │   ├── core/            # Config, transport, event queue, storage
│   │   ├── capture/         # Auto-capture (clicks, pages, forms) + manual tracking
│   │   ├── flags/           # Client-side feature flag evaluation (MurmurHash3 bucketing)
│   │   ├── sse/             # Real-time flag update stream
│   │   ├── ui/              # Server-driven UI components (banner, modal, toast, etc.)
│   │   └── privacy/         # Consent management, PII scrubbing, cookieless mode
│   └── package.json
│
├── services/
│   ├── ingestion/           # C++ (Crow) — high-performance event ingestion
│   │   ├── src/             # HTTP handlers, schema validation, Redis producer
│   │   ├── include/         # Header files
│   │   └── tests/           # GTest unit tests
│   │
│   ├── config/              # C++ (Crow) — feature flags & experiment configuration
│   │   ├── src/             # Flags CRUD, SSE broadcaster, PostgreSQL store, Redis cache
│   │   ├── include/
│   │   └── tests/
│   │
│   ├── query/               # Python (FastAPI) — analytics query engine
│   │   └── app/
│   │       ├── clickhouse/  # ClickHouse client + query builders
│   │       ├── routers/     # Funnels, cohorts, retention, experiments
│   │       └── models/      # Pydantic schemas, statistical analysis (freq/bayesian/sequential)
│   │
│   └── agents/              # Python (FastAPI + LangGraph) — autonomous AI agents
│       └── app/
│           ├── graphs/      # LangGraph workflows (supervisor, behavior, experiments, etc.)
│           ├── llm/         # LLM router + prompt templates
│           ├── memory/      # pgvector-backed agent memory
│           ├── tools/       # Agent tools (ClickHouse queries, flag/experiment CRUD, UI config)
│           └── safety/      # Action validation, rollback, audit logging
│
├── pipeline/
│   ├── redis/               # Redis Streams → ClickHouse event writer
│   ├── kafka/               # Kafka topic definitions (Phase 3+ migration)
│   └── clickhouse/          # Schemas + migrations (events, sessions, experiments, materialized views)
│
├── infra/
│   └── docker/              # Docker Compose (deps + full stack)
│
├── .github/workflows/       # CI (lint + test) and Release (npm publish + Docker images)
└── Makefile                 # Build, test, lint, migrate, and dev orchestration
```

## Tech Stack

| Layer | Technology |
|---|---|
| Client SDK | TypeScript, Rollup, Vitest |
| Ingestion & Config Services | C++17, Crow, hiredis, RapidJSON, spdlog, Conan, CMake |
| Query Service | Python 3.12, FastAPI, ClickHouse, SciPy, NumPy |
| Agents Service | Python 3.12, FastAPI, LangGraph, LiteLLM, pgvector, asyncpg |
| Event Pipeline | Redis Streams (Phase 1–2), Kafka (Phase 3+) |
| Analytics Store | ClickHouse (MergeTree, materialized views) |
| Config Store | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Infrastructure | Docker, Docker Compose |
| CI/CD | GitHub Actions |

## Getting Started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker & Docker Compose
- Node.js 20+
- Python 3.12+
- CMake 3.20+ & Conan 2.x (for C++ services, optional for local dev)

### Quick Start

```bash
make setup
```

This single command will:
1. Create isolated Python virtualenvs (via `uv`) for each service
2. Install all Python and Node.js dependencies
3. Start infrastructure containers (Redis, ClickHouse, PostgreSQL)
4. Run ClickHouse migrations
5. Copy `.env.example` → `.env` (edit to add API keys)

### Running Services

After setup, start individual services locally with hot-reload:

```bash
make run-query      # Query Service    → http://localhost:8082
make run-agents     # Agents Service   → http://localhost:8083
make run-pipeline   # ClickHouse Writer (Redis Streams consumer)
```

Or start everything via Docker (including C++ services):

```bash
make dev-all
```

### Build

```bash
make build          # Build SDK + C++ services
make build-sdk      # SDK only
make build-ingestion
make build-config
```

### Test & Lint

```bash
make test           # Run all tests
make lint           # Run all linters

make test-sdk       # SDK unit tests
make test-query     # Query service tests
make test-agents    # Agents service tests
make test-ingestion # Ingestion service tests (GTest)
make test-config    # Config service tests (GTest)

make lint-query     # ruff check on query service
make lint-agents    # ruff check on agents service
```

### Database Migrations

```bash
make migrate-clickhouse
```

### Teardown

```bash
make dev-down       # Stop all Docker containers
```

## SDK Usage

```typescript
import { APDL } from '@apdl/sdk';

const apdl = new APDL({
  apiKey: 'your-api-key',
  autoCapture: true,                     // clicks, page views, forms, scroll depth, rage clicks
  privacyMode: 'standard',              // 'standard' | 'cookieless' | 'strict'
});

// Manual event tracking
apdl.track('purchase_completed', {
  product_id: 'sku-123',
  revenue: 49.99,
});

// Feature flags (client-side evaluation)
const flag = apdl.flags.evaluate('new-checkout-flow', {
  userId: 'user-42',
  traits: { plan: 'pro', country: 'US' },
});

if (flag.value) {
  // flag.variant, flag.payload available for multi-variant experiments
}

// Identify users
apdl.identify('user-42', {
  email: 'user@example.com',
  plan: 'pro',
});
```

## Agents

The agents service runs autonomous analysis workflows powered by LLM reasoning (via LiteLLM) and LangGraph state machines.

**Agent graphs:**
- **Behavior Analysis** — queries ClickHouse to identify trends, anomalies, and conversion patterns
- **Experiment Design** — proposes A/B tests based on behavioral insights, creates flags and experiments
- **Personalization** — configures server-driven UI components per user segment
- **Feature Proposals** — generates product feature suggestions backed by data

**Autonomy levels:**
| Level | Behavior |
|---|---|
| L1 | Suggest only — surfaces insights for human review |
| L2 | Auto-safe — auto-deploys low-risk changes (e.g., <5% rollout) |
| L3 | Auto + approve risky — auto-deploys safe changes, queues risky ones for approval |
| L4 | Full auto — executes all actions with audit logging |

All agent actions go through a safety validator and are recorded in the audit log. Rollback is supported for any agent-initiated change.

## API Endpoints

### Ingestion Service (`:8080`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/events` | Ingest event batch |
| `GET` | `/health` | Health check |

### Config Service (`:8081`)
| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/flags` | Get flags for a project (SDK polling) |
| `GET` | `/v1/stream` | SSE stream for real-time flag updates |
| `GET/POST` | `/v1/admin/flags` | List / create flags |
| `PUT/DELETE` | `/v1/admin/flags/:key` | Update / delete flag |
| `GET/POST` | `/v1/admin/experiments` | List / create experiments |
| `PUT/DELETE` | `/v1/admin/experiments/:key` | Update / delete experiment |
| `GET` | `/health` | Health check |

### Query Service (`:8082`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/query/funnel` | N-step funnel analysis (windowFunnel) |
| `GET` | `/v1/query/cohorts` | Cohort analysis |
| `GET` | `/v1/query/retention` | Retention curves |
| `GET` | `/v1/query/experiment/:id` | Experiment results with statistical tests |
| `GET` | `/health` | Health / readiness |

### Agents Service (`:8083`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/agents/trigger` | Start an agent run |
| `GET` | `/v1/agents/status/:run_id` | Check run status |
| `POST` | `/v1/agents/approve/:action_id` | Approve a pending agent action |
| `GET` | `/health` | Health / readiness |

## Infrastructure

All services and dependencies run via Docker Compose:

```bash
# Dependencies only (Redis, ClickHouse, PostgreSQL)
docker compose -f infra/docker/docker-compose.deps.yml up -d

# Full stack (deps + all application services)
docker compose -f infra/docker/docker-compose.yml up --build
```

| Container | Port | Description |
|---|---|---|
| `ingestion` | 8080 | Event ingestion (C++) |
| `config` | 8081 | Feature flags & experiments (C++) |
| `query` | 8082 | Analytics queries (Python) |
| `agents` | 8083 | Autonomous AI agents (Python) |
| `clickhouse-writer` | -- | Redis Streams to ClickHouse pipeline |
| `redis` | 6379 | Event streams + cache |
| `clickhouse` | 8123 / 9000 | Analytics store (HTTP / native) |
| `postgres` | 5432 | Config store + pgvector (pgvector/pgvector:pg16) |

See `infra/docker/` for the full configuration.

## License

MIT
