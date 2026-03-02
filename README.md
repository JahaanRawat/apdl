# APDL

**Autonomous Product Development Loop** — a self-optimizing product analytics and experimentation platform. APDL ingests user behavior events, runs analytics queries, evaluates feature flags and A/B experiments, and uses LLM-powered agents to autonomously generate insights, design experiments, and personalize user experiences.

## Architecture

```
 Browser SDK                 Ingestion (C++)         Redis Streams       ClickHouse
┌──────────────┐    POST    ┌──────────────┐       ┌─────────────┐     ┌──────────┐
│  @apdl/sdk   │──────────▶│   Crow HTTP   │──────▶│ events:raw  │────▶│  events  │
│  (TypeScript) │           │  + Auth/Rate  │       │  per-project│     │  tables  │
└──────┬───────┘           └──────────────┘       └─────────────┘     └────┬─────┘
       │                                                                    │
       │  SSE     Config Service (C++)     PostgreSQL                       │
       │         ┌──────────────────┐     ┌──────────┐                     │
       ├────────▶│  Flags & Exps    │◀───▶│  flags   │      Query Service (Python)
       │         │  CRUD + SSE      │     │  exps    │     ┌──────────────┐
       │         └────────┬─────────┘     │  ui_cfgs │     │  FastAPI     │
       │                  │               └──────────┘     │  funnels     │◀─┘
       │            Redis Cache                            │  cohorts     │
       │                                                   │  retention   │
       │                                                   │  experiments │
       │         Agents Service (Python)                   └──────┬───────┘
       │        ┌───────────────────────┐                         │
       │        │  LangGraph Supervisor │◀────────────────────────┘
       │        │  ├─ Behavior Analysis │
       │        │  ├─ Experiment Design │──▶ Auto-create flags & experiments
       │        │  ├─ Personalization   │──▶ Auto-configure UI components
       │        │  └─ Feature Proposals │
       │        │  pgvector memory      │
       │        │  Safety + Audit       │
       │        └───────────────────────┘
       │
       ▼
  UI Components
  (banner, modal, toast, card, CTA)
```

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
│   ├── docker/              # Docker Compose for local dev (Redis, ClickHouse, PostgreSQL + services)
│   └── terraform/           # AWS infrastructure (EKS, ElastiCache, RDS, ClickHouse, monitoring)
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
| Infrastructure | Terraform, AWS EKS, Docker |
| CI/CD | GitHub Actions |

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Node.js 20+
- Python 3.12+
- CMake 3.20+ & Conan (for C++ services)

### Local Development

Start all infrastructure dependencies (Redis, ClickHouse, PostgreSQL):

```bash
make dev
```

Or start everything including application services:

```bash
make dev-all
```

Install SDK and Python service dependencies:

```bash
make deps
```

### Build

```bash
make build          # Build SDK + C++ services
make build-sdk      # SDK only
make build-ingestion
make build-config
```

### Test

```bash
make test           # Run all tests
make test-sdk       # SDK unit tests
make test-query     # Query service tests
make test-agents    # Agents service tests
make test-ingestion # Ingestion service tests (GTest)
make test-config    # Config service tests (GTest)
```

### Database Migrations

```bash
make migrate-clickhouse
```

### Teardown

```bash
make dev-down
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

Production deployment targets AWS:

- **Compute**: EKS (Kubernetes) with auto-scaling node groups
- **Event pipeline**: Redis Streams (ElastiCache) with Kafka migration path at scale
- **Analytics**: ClickHouse on EC2 with EBS storage
- **Config & Agent state**: PostgreSQL 16 (RDS) with pgvector
- **Monitoring**: CloudWatch, Prometheus, Grafana via Terraform modules

See `infra/terraform/` for the full IaC configuration.

## License

MIT
