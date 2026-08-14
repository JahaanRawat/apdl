# Direct Console backend setup and release proof

APDL OSS exposes one browser-visible backend origin. The separately released
Console sends `/api/*` directly to that origin; APDL OSS does not contain or
serve the Console application, its assets, or a cloud relay.

## Localhost setup

Run the fresh source-built backend and keep the gateway on its canonical
loopback address:

```bash
make setup
make dev-core
```

The browser origin is configured as an exact JSON array, not as a wildcard,
regular expression, or suffix:

```dotenv
APDL_GATEWAY_ALLOWED_HOSTS=["localhost:8000"]
APDL_GATEWAY_PUBLIC_SCHEME=http
APDL_CONSOLE_ALLOWED_ORIGINS=["https://console.apdl.dev"]
APDL_GATEWAY_HOST_PORT=8000
```

Enter `localhost:8000` in the Console connection screen. The Console first
opens `http://localhost:8000/api/console/v1/manifest`; it must not send a
password, bearer, or project request until that strict manifest is compatible.
The main Compose file publishes only gateway port 8000 for product/browser
traffic. Admin API, Ingestion, Config, Query, Agents, Codegen, and LLM Vault use
private Compose ports. Local database ports remain loopback-only developer
interfaces and are not browser boundaries.

The portable Compose file binds IPv4 loopback. On a Docker engine with IPv6
loopback support, include `infra/docker/docker-compose.ipv6-loopback.yml` in
the same Compose lifecycle to add `[::1]:8000`. Continue connecting to
`http://localhost:8000`: the overlay does not create a second backend origin or
change the exact `Host: localhost:8000` contract.

The dependency-free public verification needs no credentials:

```bash
make verify-direct-console
```

For the authenticated proof, use a disposable operator with `config:read` for
one fixture project. Read the password without putting it in shell history or
the process list:

```bash
read -rsp 'Disposable APDL password: ' APDL_PROOF_PASSWORD; echo
printf '%s\n' "$APDL_PROOF_PASSWORD" | \
  python3 scripts/verify_direct_console.py \
    --password-stdin \
    --email operator@example.com \
    --project-id demo \
    --require-auth
unset APDL_PROOF_PASSWORD
```

Add `--exercise-mutation` only for a disposable fixture project. It submits one
uniquely identified `direct_console_release_proof` event and never retries that
write. The verifier checks the manifest first, strict routing and error
envelopes, request IDs, exact CORS on success/error/SSE, bearer identity,
project flags, incremental authenticated SSE with `Last-Event-ID`, logout and
revocation, and the absence of cookies and redirects. It never prints a
password, bearer, request body, response body, or backend data. An existing
token may instead be supplied through `APDL_CONSOLE_SMOKE_BEARER`; the verifier
does not revoke a token it did not create.

The password-login proof consumes eight rate-limited `/api/*` requests, or nine
with `--exercise-mutation`; an existing-bearer proof consumes five or six.
Preflight and the `/v1/*` isolation probe do not consume the Console API budget.
The verifier never retries. Start it with that much capacity remaining in the
configured fixed window. If it receives `429`, wait for the configured window
to expire before rerunning; never repeat mutation mode against a non-disposable
project.

## Hosted HTTPS edge

Terminate browser-trusted TLS at an operator-owned edge that forwards directly
to the gateway. Do not add a hosted APDL relay, path prefix, redirect, or UI
asset route. Preserve the public `Host`, disable response buffering and
compression for SSE, and configure the gateway with the exact public host,
scheme, and Console origin:

```dotenv
APDL_GATEWAY_ALLOWED_HOSTS=["apdl-backend.example.com"]
APDL_GATEWAY_PUBLIC_SCHEME=https
APDL_CONSOLE_ALLOWED_ORIGINS=["https://console.apdl.dev"]
```

The certificate must be trusted by the browser for
`apdl-backend.example.com`. The edge must pass that exact Host; aliases and
origin-changing redirects are unsupported. Verify the deployed edge with:

```bash
APDL_CONSOLE_SMOKE_ORIGIN=https://apdl-backend.example.com \
APDL_CONSOLE_SMOKE_CONSOLE_ORIGIN=https://console.apdl.dev \
  python3 scripts/verify_direct_console.py
```

For a private Console preview, add its exact HTTPS origin to
`APDL_CONSOLE_ALLOWED_ORIGINS`, restart the gateway, and remove the preview
origin after validation. Never use `*`, `null`, a domain suffix, or
`Access-Control-Allow-Credentials`.

## Backend-first release order

1. Approve and publish the locked Console API v1 contract fixture.
2. Create a fresh deployment ID and build metadata; do not copy another
   deployment's database or `.env`.
3. Apply PostgreSQL migrations before starting Admin API.
4. Start internal product services and Admin API without host-published product
   ports.
5. Start the gateway with the final exact Host and Console-origin arrays.
6. Run `make verify-release`,
   `docker compose -f infra/docker/docker-compose.yml config --quiet`, and the
   public and authenticated direct-console verifier modes.
7. Release this backend capability before enabling or publishing a connecting
   Console.

Rollback of the Console or `/api/*` capability does not change registered SDK
`/v1/events`, `/v1/flags`, or `/v1/stream` traffic. Do not reuse a bearer across
deployment IDs.

## Evidence boundary

This verifier and CI prove the HTTP, CORS, routing, authentication, and SSE
backend contracts without a browser. Final release evidence still requires the
declared real-browser matrix: local HTTP in Chrome, Edge, and Firefox desktop;
hosted trusted HTTPS including real Safari; local-network permission allow and
deny; CSP; backend switching; expiry; and storage/URL/log inspection. Browser
engine emulation is not a substitute for that evidence.
