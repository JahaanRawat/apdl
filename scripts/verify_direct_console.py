#!/usr/bin/env python3
"""Credential-safe release verifier for the direct-console backend boundary."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import ssl
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlsplit


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MANIFEST_PATH = "/api/console/v1/manifest"
ERROR_FIELDS = {"schema_version", "code", "message", "request_id"}
MANIFEST_FIELDS = {
    "schema_version",
    "deployment_id",
    "display_name",
    "backend_version",
    "build_revision",
    "console_api_version",
}
SESSION_FIELDS = {"schema_version", "access_token", "expires_at"}
IDENTITY_FIELDS = {"schema_version", "user_id", "email", "projects"}
PROJECT_FIELDS = {"project_id", "roles"}
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HUMAN_ROLES = {
    "events:write",
    "config:read",
    "config:write",
    "config:evaluate",
    "query:read",
    "agents:read",
    "agents:run",
    "agents:manage",
    "agents:approve",
    "credentials:manage",
    "members:manage",
}
SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
CORS_HEADERS = {
    "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type, Last-Event-ID",
    "access-control-expose-headers": "X-Request-ID",
    "access-control-max-age": "600",
    "vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
}


class VerificationFailure(RuntimeError):
    """The backend did not satisfy the direct-console release contract."""


@dataclass(frozen=True)
class BoundaryResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def values(self, name: str) -> list[str]:
        lowered = name.lower()
        return [value for key, value in self.headers if key.lower() == lowered]


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        *,
        first_sse_event: bool = False,
    ) -> BoundaryResponse: ...


def canonical_backend_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        raise VerificationFailure("Backend origin must be one canonical HTTP(S) origin")
    hostname = parsed.hostname
    try:
        hostname.encode("ascii")
        port = parsed.port
    except (UnicodeEncodeError, ValueError) as exc:
        raise VerificationFailure("Backend origin has an invalid host or port") from exc
    if hostname != hostname.lower():
        raise VerificationFailure("Backend origin host must be lowercase")
    if parsed.scheme == "http" and (hostname != "localhost" or port != 8000):
        raise VerificationFailure(
            "Plain HTTP is supported only for http://localhost:8000"
        )
    if (parsed.scheme, port) in {("http", 80), ("https", 443)}:
        raise VerificationFailure("Backend origin must omit its default port")
    canonical = f"{parsed.scheme}://{hostname}"
    if port is not None:
        canonical = f"{canonical}:{port}"
    if value != canonical:
        raise VerificationFailure(f"Backend origin is not canonical: {canonical}")
    return canonical


def canonical_console_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        raise VerificationFailure("Console Origin must be one canonical HTTP(S) origin")
    try:
        parsed.hostname.encode("ascii")
        port = parsed.port
    except (UnicodeEncodeError, ValueError) as exc:
        raise VerificationFailure("Console Origin has an invalid host or port") from exc
    canonical = f"{parsed.scheme}://{parsed.hostname}"
    if port is not None:
        if (parsed.scheme, port) in {("http", 80), ("https", 443)}:
            raise VerificationFailure("Console Origin must omit its default port")
        canonical = f"{canonical}:{port}"
    if value != canonical:
        raise VerificationFailure(f"Console Origin is not canonical: {canonical}")
    return canonical


class HTTPTransport:
    def __init__(self, origin: str, timeout: float) -> None:
        self.origin = canonical_backend_origin(origin)
        self.timeout = timeout
        self.parsed = urlsplit(self.origin)

    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        *,
        first_sse_event: bool = False,
    ) -> BoundaryResponse:
        connection_class = (
            http.client.HTTPSConnection
            if self.parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.parsed.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = connection_class(self.parsed.hostname, self.parsed.port, **kwargs)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_headers = tuple(response.getheaders())
            response_body = (
                _read_first_sse_event(response)
                if first_sse_event
                else _read_bounded(response)
            )
            return BoundaryResponse(response.status, response_headers, response_body)
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise VerificationFailure(
                f"Backend request failed for {self.origin}{path}: {type(exc).__name__}"
            ) from exc
        finally:
            connection.close()


def _read_bounded(response: http.client.HTTPResponse) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise VerificationFailure("Backend response exceeded the verifier body limit")
    return body


def _read_first_sse_event(response: http.client.HTTPResponse) -> bytes:
    body = bytearray()
    while len(body) <= MAX_RESPONSE_BYTES:
        line = response.readline(MAX_RESPONSE_BYTES - len(body) + 1)
        if not line:
            raise VerificationFailure("SSE closed before its first complete event")
        body.extend(line)
        if line in {b"\n", b"\r\n"}:
            return bytes(body)
    raise VerificationFailure("First SSE event exceeded the verifier body limit")


def _json_object(response: BoundaryResponse, label: str) -> dict[str, Any]:
    content_type = _one_header(response, "content-type", label)
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise VerificationFailure(f"{label} did not return application/json")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"{label} did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise VerificationFailure(f"{label} must return one JSON object")
    return value


def _exact_fields(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise VerificationFailure(
            f"{label} fields differ: missing={sorted(fields - set(value))}, "
            f"unknown={sorted(set(value) - fields)}"
        )


def _one_header(response: BoundaryResponse, name: str, label: str) -> str:
    values = response.values(name)
    if len(values) != 1:
        raise VerificationFailure(f"{label} must return exactly one {name} header")
    return values[0]


class DirectConsoleVerifier:
    def __init__(
        self,
        transport: Transport,
        *,
        origin: str,
        console_origin: str,
    ) -> None:
        self.transport = transport
        self.origin = canonical_backend_origin(origin)
        self.console_origin = canonical_console_origin(console_origin)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        first_sse_event: bool = False,
    ) -> BoundaryResponse:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise VerificationFailure("Verifier paths must be absolute and query-free")
        if token is not None and not path.startswith("/api/"):
            raise VerificationFailure("Human bearer tokens may be sent only to /api/*")
        request_id = str(uuid.uuid4())
        headers = {
            "Accept": "text/event-stream" if first_sse_event else "application/json",
            "Origin": self.console_origin,
            "X-Request-ID": request_id,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        response = self.transport.request(
            method,
            path,
            headers,
            body,
            first_sse_event=first_sse_event,
        )
        self._assert_boundary_response(response, path, request_id)
        return response

    def _assert_boundary_response(
        self,
        response: BoundaryResponse,
        path: str,
        request_id: str,
    ) -> None:
        if 300 <= response.status < 400 or response.values("location"):
            raise VerificationFailure(f"Redirects are forbidden at {path}")
        if response.values("set-cookie"):
            raise VerificationFailure(f"Cookies are forbidden at {path}")
        if _one_header(response, "x-request-id", path) != request_id:
            raise VerificationFailure(
                f"{path} did not preserve the canonical request ID"
            )
        if path.startswith("/api/"):
            expected = {
                **CORS_HEADERS,
                "access-control-allow-origin": self.console_origin,
            }
            for name, value in expected.items():
                if _one_header(response, name, path) != value:
                    raise VerificationFailure(f"{path} returned incorrect {name}")
            if response.values("access-control-allow-credentials"):
                raise VerificationFailure("Console CORS must never allow credentials")

    def verify_manifest(self) -> dict[str, Any]:
        response = self.request("GET", MANIFEST_PATH)
        if response.status != 200:
            raise VerificationFailure(f"Manifest returned status {response.status}")
        if _one_header(response, "cache-control", "manifest") != "no-store":
            raise VerificationFailure("Manifest must use Cache-Control: no-store")
        value = _json_object(response, "manifest")
        _exact_fields(value, MANIFEST_FIELDS, "manifest")
        try:
            deployment_id = str(uuid.UUID(str(value["deployment_id"])))
        except ValueError as exc:
            raise VerificationFailure("Manifest deployment ID is not a UUID") from exc
        if (
            value["schema_version"] != "console_manifest@1"
            or value["console_api_version"] != 1
            or deployment_id != value["deployment_id"]
            or not isinstance(value["display_name"], str)
            or not 1 <= len(value["display_name"]) <= 100
            or any(
                ord(character) <= 31 or ord(character) == 127
                for character in value["display_name"]
            )
            or not isinstance(value["backend_version"], str)
            or SEMVER_PATTERN.fullmatch(value["backend_version"]) is None
            or not isinstance(value["build_revision"], str)
            or re.fullmatch(r"[0-9a-f]{40}", value["build_revision"]) is None
        ):
            raise VerificationFailure("Manifest values do not match console_manifest@1")
        return value

    def verify_public_boundary(self) -> None:
        preflight = self.request(
            "OPTIONS",
            "/api/console/v1/session",
            extra_headers={
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, Last-Event-ID",
            },
        )
        if preflight.status != 204 or preflight.body:
            raise VerificationFailure("Console preflight must return an empty 204")

        api_error = self.request("GET", "/api/console/v1/not-a-route")
        self._assert_error(api_error, 404, "API route error")
        for path in (
            "/",
            "/index.html",
            "/assets/release-proof.js",
            "/v1/not-registered",
        ):
            response = self.request("GET", path)
            self._assert_error(response, 404, path)

    def login(self, email: str, password: str) -> str:
        response = self.request(
            "POST",
            "/api/console/v1/sessions",
            payload={"email": email, "password": password},
        )
        if response.status != 200:
            raise VerificationFailure(
                f"Console login returned status {response.status}"
            )
        value = _json_object(response, "console session")
        _exact_fields(value, SESSION_FIELDS, "console session")
        token = value.get("access_token")
        if (
            value.get("schema_version") != "console_session@1"
            or not isinstance(token, str)
            or TOKEN_PATTERN.fullmatch(token) is None
        ):
            raise VerificationFailure(
                "Console login returned an invalid session contract"
            )
        try:
            expires_at = datetime.fromisoformat(
                str(value.get("expires_at")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise VerificationFailure("Console session expiry is not RFC 3339") from exc
        if expires_at.tzinfo is None:
            raise VerificationFailure("Console session expiry must include a timezone")
        return token

    def identity(self, token: str) -> dict[str, Any]:
        response = self.request("GET", "/api/console/v1/session", token=token)
        if response.status != 200:
            raise VerificationFailure(
                f"Console identity returned status {response.status}"
            )
        value = _json_object(response, "console identity")
        _exact_fields(value, IDENTITY_FIELDS, "console identity")
        user_id = value.get("user_id")
        email = value.get("email")
        try:
            canonical_user_id = str(uuid.UUID(str(user_id)))
        except ValueError as exc:
            raise VerificationFailure("Console identity user ID is not a UUID") from exc
        if (
            value.get("schema_version") != "console_identity@1"
            or canonical_user_id != user_id
            or not isinstance(email, str)
            or not 1 <= len(email) <= 320
            or email.count("@") != 1
            or any(character.isspace() or ord(character) < 32 for character in email)
            or not isinstance(value.get("projects"), list)
        ):
            raise VerificationFailure(
                "Console identity has an invalid version or projects"
            )
        for project in value["projects"]:
            if not isinstance(project, dict):
                raise VerificationFailure("Console project access must be an object")
            _exact_fields(project, PROJECT_FIELDS, "console project access")
            if (
                not isinstance(project.get("project_id"), str)
                or PROJECT_ID_PATTERN.fullmatch(project["project_id"]) is None
                or not isinstance(project.get("roles"), list)
                or not project["roles"]
                or any(
                    not isinstance(role, str) or role not in HUMAN_ROLES
                    for role in project["roles"]
                )
                or len(project["roles"]) != len(set(project["roles"]))
            ):
                raise VerificationFailure("Console project access is invalid")
        return value

    def select_stream_project(
        self, identity: Mapping[str, Any], requested: str | None
    ) -> str:
        projects = identity["projects"]
        if requested is not None and PROJECT_ID_PATTERN.fullmatch(requested) is None:
            raise VerificationFailure("--project-id must be a canonical project ID")
        for project in projects:
            if requested is not None and project["project_id"] != requested:
                continue
            if "config:read" in project["roles"]:
                return project["project_id"]
        label = requested or "any identity project"
        raise VerificationFailure(
            f"No config:read stream authority is available for {label}"
        )

    def verify_project(self, token: str, project_id: str, *, mutation: bool) -> None:
        encoded = quote(project_id, safe="")
        flags = self.request(
            "GET", f"/api/projects/{encoded}/config/v1/flags", token=token
        )
        if flags.status != 200:
            raise VerificationFailure(f"Project flags returned status {flags.status}")
        _json_object(flags, "project flags")

        stream = self.request(
            "GET",
            f"/api/projects/{encoded}/config/v1/stream",
            token=token,
            extra_headers={"Last-Event-ID": "direct-console-release-proof"},
            first_sse_event=True,
        )
        if stream.status != 200:
            raise VerificationFailure(f"Console stream returned status {stream.status}")
        if _one_header(stream, "content-type", "console stream") != "text/event-stream":
            raise VerificationFailure("Console stream content type is not exact")
        if _one_header(stream, "cache-control", "console stream") != (
            "no-cache, no-transform"
        ):
            raise VerificationFailure("Console stream cache policy is not exact")
        if _one_header(stream, "x-accel-buffering", "console stream") != "no":
            raise VerificationFailure("Console stream buffering is not disabled")
        lines = stream.body.replace(b"\r\n", b"\n").split(b"\n")
        if b"event: config" not in lines or not any(
            line.startswith(b"id: ") for line in lines
        ):
            raise VerificationFailure(
                "Console stream did not deliver a versioned config event"
            )

        sdk_probe = self.request("GET", "/v1/flags")
        if sdk_probe.status not in {401, 403}:
            raise VerificationFailure(
                "Unauthenticated SDK route did not reject the probe"
            )

        if mutation:
            now = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            run_id = uuid.uuid4().hex
            event = self.request(
                "POST",
                f"/api/projects/{encoded}/ingestion/v1/events",
                token=token,
                payload={
                    "events": [
                        {
                            "event": "direct_console_release_proof",
                            "type": "track",
                            "anonymous_id": f"release-proof-{run_id}",
                            "timestamp": now,
                            "properties": {"source": "direct-console-release-proof"},
                            "context": {
                                "library": {
                                    "name": "apdl-direct-console-verifier",
                                    "version": "1",
                                }
                            },
                            "message_id": f"direct_console_release_proof_{run_id}",
                        }
                    ]
                },
            )
            if event.status != 202 or _json_object(event, "event mutation") != {
                "accepted": 1
            }:
                raise VerificationFailure(
                    "Project mutation was not accepted exactly once"
                )

    def logout(self, token: str) -> None:
        response = self.request("DELETE", "/api/console/v1/session", token=token)
        if response.status != 204 or response.body:
            raise VerificationFailure("Logout must return an empty 204")
        expired = self.request("GET", "/api/console/v1/session", token=token)
        self._assert_error(expired, 401, "revoked console session")

    def _assert_error(
        self, response: BoundaryResponse, status: int, label: str
    ) -> dict[str, Any]:
        if response.status != status:
            raise VerificationFailure(f"{label} returned status {response.status}")
        value = _json_object(response, label)
        _exact_fields(value, ERROR_FIELDS, label)
        request_id = _one_header(response, "x-request-id", label)
        if (
            value.get("schema_version") != "error@1"
            or value.get("request_id") != request_id
            or not isinstance(value.get("code"), str)
            or ERROR_CODE_PATTERN.fullmatch(value["code"]) is None
            or not isinstance(value.get("message"), str)
            or not 1 <= len(value["message"]) <= 1024
        ):
            raise VerificationFailure(
                f"{label} did not return the strict error@1 contract"
            )
        return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a direct-console backend without logging credentials"
    )
    parser.add_argument(
        "--origin",
        default=os.getenv("APDL_CONSOLE_SMOKE_ORIGIN", "http://localhost:8000"),
    )
    parser.add_argument(
        "--console-origin",
        default=os.getenv(
            "APDL_CONSOLE_SMOKE_CONSOLE_ORIGIN", "https://console.apdl.dev"
        ),
    )
    parser.add_argument("--email", default=os.getenv("APDL_CONSOLE_SMOKE_EMAIL"))
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument(
        "--project-id", default=os.getenv("APDL_CONSOLE_SMOKE_PROJECT_ID")
    )
    parser.add_argument("--exercise-mutation", action="store_true")
    parser.add_argument("--require-auth", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def _credentials(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    bearer = os.getenv("APDL_CONSOLE_SMOKE_BEARER")
    password = os.getenv("APDL_CONSOLE_SMOKE_PASSWORD")
    if args.password_stdin:
        if password is not None:
            raise VerificationFailure("Choose password stdin or environment, not both")
        password = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
    if bearer and (args.email or password):
        raise VerificationFailure("Choose an existing bearer or email/password login")
    if (args.email is None) != (password is None):
        raise VerificationFailure("Email and password must be supplied together")
    if bearer is not None and TOKEN_PATTERN.fullmatch(bearer) is None:
        raise VerificationFailure("APDL_CONSOLE_SMOKE_BEARER has an invalid shape")
    return args.email, password, bearer


def run(args: argparse.Namespace, transport: Transport | None = None) -> None:
    if not isinstance(args.timeout, int | float) or args.timeout <= 0:
        raise VerificationFailure("--timeout must be positive")
    origin = canonical_backend_origin(args.origin)
    email, password, existing_bearer = _credentials(args)
    if args.require_auth and email is None and existing_bearer is None:
        raise VerificationFailure("Authenticated verification credentials are required")
    if (
        (args.project_id or args.exercise_mutation)
        and email is None
        and existing_bearer is None
    ):
        raise VerificationFailure("Project verification requires authentication")

    verifier = DirectConsoleVerifier(
        transport or HTTPTransport(origin, args.timeout),
        origin=origin,
        console_origin=args.console_origin,
    )
    verifier.verify_manifest()
    print(f"  ok  manifest-first compatibility at {origin}{MANIFEST_PATH}")
    verifier.verify_public_boundary()
    print("  ok  strict routing, errors, request IDs, CORS, and no cookies/redirects")

    token = existing_bearer
    owns_token = False
    if email is not None and password is not None:
        token = verifier.login(email, password)
        owns_token = True
    if token is None:
        print("  ok  public direct-console boundary verified (authentication skipped)")
        return

    primary_failure: BaseException | None = None
    try:
        identity = verifier.identity(token)
        project_id = verifier.select_stream_project(identity, args.project_id)
        verifier.verify_project(token, project_id, mutation=args.exercise_mutation)
        print("  ok  bearer identity, project read, and authenticated SSE")
        if args.exercise_mutation:
            print("  ok  registered project mutation accepted exactly once")
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        if owns_token:
            try:
                verifier.logout(token)
                print("  ok  logout revoked the fixed bearer session")
            except BaseException as logout_error:
                if primary_failure is None:
                    raise
                primary_failure.add_note(
                    f"logout cleanup also failed: {type(logout_error).__name__}"
                )


def main() -> int:
    try:
        run(_parser().parse_args())
    except VerificationFailure as exc:
        print(f"direct-console verification failed: {exc}", file=sys.stderr)
        return 1
    print("Direct-console backend release proof passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
