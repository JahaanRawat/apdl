# Unified Gateway

The gateway is APDL's single browser-visible backend boundary. It forwards the
complete `/api/*` path to the Admin API and retains only the registered SDK
routes `/v1/events`, `/v1/flags`, and `/v1/stream`. Every other path returns a
strict `error@1` response; this service never contains or serves console assets.

The source-built Compose stack publishes it as `http://localhost:8000` on IPv4
loopback. Admin API and product services remain private on the Compose network.
The gateway-to-Admin hop uses a dedicated internal network and one exact trusted
gateway address so caller-supplied forwarding chains cannot become Admin client
identity.

## Development

```bash
make test-gateway
make lint-gateway
make run-gateway
```

`APDL_GATEWAY_ALLOWED_HOSTS` is a required-shape JSON array and defaults to
`["localhost:8000"]`. `APDL_GATEWAY_TRUSTED_PROXY_CIDRS` accepts only a JSON
array of unique canonical networks and defaults to `[]`; the gateway trusts one
forwarded client address only when the immediate socket peer belongs to that
allowlist. Upstream origins, request limits, connection/read/write timeouts, and
pool bounds are independently configurable through the strict settings in
`app/config.py`.

Actual `/api/*` requests use a bounded in-memory per-client fixed-window limit.
`APDL_GATEWAY_API_RATE_LIMIT`, `APDL_GATEWAY_API_RATE_WINDOW_SECONDS`, and
`APDL_GATEWAY_API_RATE_MAX_CLIENTS` configure positive request, time, and memory
bounds. SDK `/v1/*` routes and CORS preflights do not consume this budget.

Hosts whose Docker engine supports IPv6 loopback can add the optional overlay;
the portable base remains IPv4-only:

```bash
docker compose --env-file .env \
  -f infra/docker/docker-compose.yml \
  -f infra/docker/docker-compose.ipv6-loopback.yml \
  up -d --build
```

The overlay adds a `[::1]:8000` publication while retaining
`127.0.0.1:8000`. Continue using `http://localhost:8000` so the exact default
Host contract remains `localhost:8000`.

Exact-origin console CORS is intentionally not configured in this gateway
revision; it is a separate deployment contract and must not be approximated
with wildcard or suffix matching.
