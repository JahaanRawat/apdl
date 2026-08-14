# APDL Console API v1 contracts

This directory is the source of truth for the public browser contract exposed
by an APDL OSS backend. The separately distributed console must generate or
validate its runtime types from these files and treat contract drift as a
release failure.

- `openapi.json` defines the public compatibility and browser-session routes,
  plus the authenticated Config stream exposed through the Admin BFF.
- `schemas/` contains the canonical JSON Schema Draft 2020-12 documents used by
  those operations and stream control events.
- Every object schema rejects unknown fields. Schema versions and the console
  API version are exact constants; there are no fallback shapes or aliases.

`GET /api/console/v1/session` returns exactly a `console_identity@1` payload.
The `console_stream_control@1` payload is emitted as SSE `data` when the BFF
must terminate a protected stream because session or project authority is no
longer usable. All stream-control fields are present for every code so clients
do not need code-specific fallback shapes.
