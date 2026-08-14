# Unified Gateway

The gateway is APDL's single browser-visible backend boundary. It forwards the
complete `/api/*` path to the Admin API and retains only the registered SDK
routes `/v1/events`, `/v1/flags`, and `/v1/stream`. Every other path returns a
strict `error@1` response; this service never contains or serves console assets.

The source-built Compose stack publishes it as `http://localhost:8000` on IPv4
loopback. Admin API and product services remain private on the Compose network.

## Development

```bash
make test-gateway
make lint-gateway
make run-gateway
```

`APDL_GATEWAY_ALLOWED_HOSTS` is a required-shape JSON array and defaults to
`["localhost:8000"]`. Upstream origins, request limits, connection/read/write
timeouts, and pool bounds are independently configurable through the strict
settings in `app/config.py`.

Exact-origin console CORS is intentionally not configured in this gateway
revision; it is a separate deployment contract and must not be approximated
with wildcard or suffix matching.
