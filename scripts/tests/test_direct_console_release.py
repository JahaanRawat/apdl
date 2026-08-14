"""Unit and static contracts for the direct-console backend release proof."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_direct_console", ROOT / "scripts" / "verify_direct_console.py"
)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)

TOKEN = "T" * 43
PASSWORD = "release-proof-password"
CONSOLE_ORIGIN = "https://console.apdl.dev"


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.logged_out = False

    def request(
        self,
        method,
        path,
        headers,
        body,
        *,
        first_sse_event=False,
    ):
        request = {
            "method": method,
            "path": path,
            "headers": dict(headers),
            "body": body,
            "first_sse_event": first_sse_event,
        }
        self.requests.append(request)
        request_id = headers["X-Request-ID"]
        common = [("X-Request-ID", request_id)]
        if path.startswith("/api/"):
            common.extend(
                [
                    ("Access-Control-Allow-Origin", CONSOLE_ORIGIN),
                    (
                        "Access-Control-Allow-Methods",
                        "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    ),
                    (
                        "Access-Control-Allow-Headers",
                        "Authorization, Content-Type, Last-Event-ID",
                    ),
                    ("Access-Control-Expose-Headers", "X-Request-ID"),
                    ("Access-Control-Max-Age", "600"),
                    (
                        "Vary",
                        "Origin, Access-Control-Request-Method, "
                        "Access-Control-Request-Headers",
                    ),
                ]
            )

        def json_response(status: int, value: dict, *extra):
            return verify.BoundaryResponse(
                status,
                tuple([*common, ("Content-Type", "application/json"), *extra]),
                json.dumps(value, separators=(",", ":")).encode(),
            )

        def error(status: int, code: str):
            return json_response(
                status,
                {
                    "schema_version": "error@1",
                    "code": code,
                    "message": "Safe release-proof error.",
                    "request_id": request_id,
                },
            )

        if path == verify.MANIFEST_PATH:
            return json_response(
                200,
                {
                    "schema_version": "console_manifest@1",
                    "deployment_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
                    "display_name": "Release Proof",
                    "backend_version": "0.3.4",
                    "build_revision": "a" * 40,
                    "console_api_version": 1,
                },
                ("Cache-Control", "no-store"),
            )
        if method == "OPTIONS":
            return verify.BoundaryResponse(204, tuple(common), b"")
        if path == "/api/console/v1/not-a-route":
            return error(404, "route_not_found")
        if path in {
            "/",
            "/index.html",
            "/assets/release-proof.js",
            "/v1/not-registered",
        }:
            return error(404, "route_not_found")
        if path == "/api/console/v1/sessions":
            assert json.loads(body) == {
                "email": "operator@example.com",
                "password": PASSWORD,
            }
            return json_response(
                200,
                {
                    "schema_version": "console_session@1",
                    "access_token": TOKEN,
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            )
        if path == "/api/console/v1/session" and method == "DELETE":
            self.logged_out = True
            return verify.BoundaryResponse(204, tuple(common), b"")
        if path == "/api/console/v1/session" and self.logged_out:
            return error(401, "session_expired")
        if path == "/api/console/v1/session":
            return json_response(
                200,
                {
                    "schema_version": "console_identity@1",
                    "user_id": "20000000-0000-4000-8000-000000000002",
                    "email": "operator@example.com",
                    "projects": [
                        {
                            "project_id": "demo",
                            "roles": ["events:write", "config:read"],
                        }
                    ],
                },
            )
        if path == "/api/projects/demo/config/v1/flags":
            return json_response(200, {"schema_version": 2, "flags": []})
        if path == "/api/projects/demo/config/v1/stream":
            return verify.BoundaryResponse(
                200,
                tuple(
                    [
                        *common,
                        ("Content-Type", "text/event-stream"),
                        ("Cache-Control", "no-cache, no-transform"),
                        ("X-Accel-Buffering", "no"),
                    ]
                ),
                b"id: 7\r\nevent: config\r\ndata: {}\r\n\r\n",
            )
        if path == "/v1/flags":
            return json_response(401, {"detail": "missing API key"})
        if path == "/api/projects/demo/ingestion/v1/events":
            payload = json.loads(body)
            assert payload["events"][0]["event"] == "direct_console_release_proof"
            return json_response(202, {"accepted": 1})
        raise AssertionError(f"Unexpected request: {method} {path}")


def args(**changes) -> argparse.Namespace:
    values = {
        "origin": "http://localhost:8000",
        "console_origin": CONSOLE_ORIGIN,
        "email": "operator@example.com",
        "password_stdin": False,
        "project_id": "demo",
        "exercise_mutation": True,
        "require_auth": True,
        "timeout": 1.0,
    }
    values.update(changes)
    return argparse.Namespace(**values)


class DirectConsoleReleaseTests(unittest.TestCase):
    def test_make_target_and_runbook_cover_local_hosted_and_manual_evidence(
        self,
    ) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        runbook = (ROOT / "docs" / "direct-console-backend.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "verify-direct-console:\n\t@python3 scripts/verify_direct_console.py $(ARGS)",
            makefile,
        )
        for required in (
            "http://localhost:8000/api/console/v1/manifest",
            'APDL_GATEWAY_ALLOWED_HOSTS=["localhost:8000"]',
            'APDL_GATEWAY_ALLOWED_HOSTS=["apdl-backend.example.com"]',
            'APDL_CONSOLE_ALLOWED_ORIGINS=["https://console.apdl.dev"]',
            "--password-stdin",
            "infra/docker/docker-compose.ipv6-loopback.yml",
            "eight rate-limited `/api/*` requests",
            "existing-bearer proof consumes five or six",
            "The verifier never retries",
            "Backend-first release order",
            "docker compose -f infra/docker/docker-compose.yml config --quiet",
            "real Safari",
            "engine emulation is not a substitute",
        ):
            self.assertIn(required, runbook)

    def test_full_verifier_is_manifest_first_and_never_leaks_credentials(self) -> None:
        transport = FakeTransport()
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"APDL_CONSOLE_SMOKE_PASSWORD": PASSWORD},
                clear=True,
            ),
            contextlib.redirect_stdout(output),
        ):
            verify.run(args(), transport)

        paths = [request["path"] for request in transport.requests]
        self.assertEqual(paths[0], verify.MANIFEST_PATH)
        self.assertTrue(transport.logged_out)
        self.assertIn("/api/projects/demo/config/v1/stream", paths)
        self.assertIn("/api/projects/demo/ingestion/v1/events", paths)
        self.assertEqual(
            paths.count("/api/projects/demo/ingestion/v1/events"),
            1,
        )
        stream = next(
            request
            for request in transport.requests
            if request["path"].endswith("/config/v1/stream")
        )
        self.assertEqual(
            stream["headers"]["Last-Event-ID"], "direct-console-release-proof"
        )
        self.assertTrue(stream["first_sse_event"])
        for request in transport.requests:
            authorization = request["headers"].get("Authorization")
            if authorization is not None:
                self.assertTrue(request["path"].startswith("/api/"))
        sdk = next(
            request for request in transport.requests if request["path"] == "/v1/flags"
        )
        self.assertNotIn("Authorization", sdk["headers"])
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertNotIn(TOKEN, output.getvalue())

    def test_public_verification_needs_no_credentials(self) -> None:
        transport = FakeTransport()
        with patch.dict(os.environ, {}, clear=True):
            verify.run(
                args(
                    email=None,
                    project_id=None,
                    exercise_mutation=False,
                    require_auth=False,
                ),
                transport,
            )

        self.assertEqual(transport.requests[0]["path"], verify.MANIFEST_PATH)
        self.assertFalse(transport.logged_out)
        self.assertNotIn(
            "/api/console/v1/sessions", [r["path"] for r in transport.requests]
        )

    def test_verifier_refuses_bearer_attachment_outside_api(self) -> None:
        verifier = verify.DirectConsoleVerifier(
            FakeTransport(),
            origin="http://localhost:8000",
            console_origin=CONSOLE_ORIGIN,
        )
        with self.assertRaisesRegex(verify.VerificationFailure, "only to /api"):
            verifier.request("GET", "/v1/flags", token=TOKEN)

    def test_redirects_and_cookies_fail_before_body_diagnostics(self) -> None:
        class UnsafeTransport:
            def __init__(self, headers):
                self.headers = headers

            def request(self, method, path, headers, body, *, first_sse_event=False):
                return verify.BoundaryResponse(
                    302
                    if any(k.lower() == "location" for k, _ in self.headers)
                    else 200,
                    tuple([("X-Request-ID", headers["X-Request-ID"]), *self.headers]),
                    b"credential-shaped-body-must-not-be-reported",
                )

        for unsafe, message in (
            ([("Location", "https://elsewhere.example")], "Redirects"),
            ([("Set-Cookie", "session=secret")], "Cookies"),
        ):
            verifier = verify.DirectConsoleVerifier(
                UnsafeTransport(unsafe),
                origin="http://localhost:8000",
                console_origin=CONSOLE_ORIGIN,
            )
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(
                    verify.VerificationFailure, message
                ) as raised:
                    verifier.request("GET", "/")
                self.assertNotIn("credential-shaped", str(raised.exception))

    def test_origins_reject_paths_credentials_and_remote_plaintext(self) -> None:
        for origin in (
            "http://localhost",
            "http://localhost:8001",
            "http://backend.example.com",
            "https://user:password@backend.example.com",
            "https://backend.example.com/path",
            "https://backend.example.com?query=x",
            "https://BACKEND.example.com",
            "https://backend.example.com:443",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(verify.VerificationFailure):
                    verify.canonical_backend_origin(origin)

        self.assertEqual(
            verify.canonical_backend_origin("http://localhost:8000"),
            "http://localhost:8000",
        )
        self.assertEqual(
            verify.canonical_backend_origin("https://backend.example.com"),
            "https://backend.example.com",
        )
        for origin in (
            "null",
            "https://console.apdl.dev/",
            "https://user:secret@console.apdl.dev",
            "https://CONSOLE.apdl.dev",
            "https://console.apdl.dev:443",
        ):
            with self.subTest(console_origin=origin):
                with self.assertRaises(verify.VerificationFailure):
                    verify.canonical_console_origin(origin)

    def test_cli_has_no_password_value_argument(self) -> None:
        help_text = verify._parser().format_help()
        self.assertIn("--password-stdin", help_text)
        self.assertNotIn("--password PASSWORD", help_text)

    def test_sse_reader_returns_after_one_frame_and_is_bounded(self) -> None:
        response = io.BytesIO(b"id: 7\r\ndata: first\r\n\r\ndata: later\r\n\r\n")
        self.assertEqual(
            verify._read_first_sse_event(response),
            b"id: 7\r\ndata: first\r\n\r\n",
        )


if __name__ == "__main__":
    unittest.main()
