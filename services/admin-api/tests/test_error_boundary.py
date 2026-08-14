from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app import proxy
from app.error_boundary import install_error_boundary
from app.request_body_limit import RequestBodyLimitMiddleware

REQUEST_ID = "87fab7d6-dba0-4f77-8ffd-00e815fc7303"
SECRET = "never-return-this-secret"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int


def _boundary_app() -> FastAPI:
    app = FastAPI(redirect_slashes=False)

    @app.get("/api/test/success")
    async def success() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/test/http/{status_code}")
    async def http_failure(status_code: int) -> None:
        headers = (
            {"Retry-After": "37", "X-Internal-Secret": SECRET}
            if status_code == 429
            else None
        )
        raise HTTPException(
            status_code=status_code,
            detail=f"raw detail {SECRET}",
            headers=headers,
        )

    @app.post("/api/test/validation")
    async def validation(_body: StrictBody) -> None:
        return None

    @app.get("/api/test/unhandled")
    async def unhandled() -> None:
        raise RuntimeError(f"database password={SECRET}")

    @app.get("/api/test/raw-upstream")
    async def raw_upstream() -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": SECRET, "internal_url": "http://private:8080"},
        )

    @app.get("/api/test/upstream-response/{status_code}")
    async def upstream_response(status_code: int) -> None:
        response = httpx.Response(
            status_code,
            content=f'{{"detail":"{SECRET}"}}'.encode(),
            headers={"Retry-After": "19", "X-Upstream-Secret": SECRET},
        )
        raise proxy._upstream_response_exception(response)

    @app.get("/api/test/upstream-transport/{kind}")
    async def upstream_transport(kind: str) -> None:
        upstream_request = httpx.Request("GET", "http://private:8080/secret")
        exception: httpx.RequestError
        if kind == "timeout":
            exception = httpx.ReadTimeout(SECRET, request=upstream_request)
        else:
            exception = httpx.ConnectError(SECRET, request=upstream_request)
        raise proxy._upstream_transport_exception(exception)

    install_error_boundary(app)
    return app


@pytest.fixture
def client() -> TestClient:
    with TestClient(_boundary_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _assert_error(
    response,
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str = REQUEST_ID,
) -> None:
    assert response.status_code == status_code
    assert response.headers["x-request-id"] == request_id
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": "error@1",
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    serialized = response.text
    assert SECRET not in serialized
    assert "detail" not in serialized
    assert "internal_url" not in serialized


@pytest.mark.parametrize(
    ("status_code", "code", "message"),
    [
        (401, "unauthorized", "Authentication is required."),
        (
            403,
            "forbidden",
            "You do not have permission to perform this action.",
        ),
        (404, "not_found", "The requested resource was not found."),
    ],
)
def test_http_exceptions_use_one_safe_strict_envelope(
    client: TestClient,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = client.get(
        f"/api/test/http/{status_code}",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=status_code,
        code=code,
        message=message,
    )


def test_validation_errors_never_return_raw_inputs_or_pydantic_details(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/test/validation",
        headers={"X-Request-ID": REQUEST_ID},
        json={"count": SECRET, "password": SECRET},
    )

    _assert_error(
        response,
        status_code=422,
        code="validation_error",
        message="The request does not match the required schema.",
    )
    assert "errors" not in response.text
    assert "input" not in response.text


def test_rate_limit_keeps_retry_after_only_as_a_header(client: TestClient) -> None:
    response = client.get(
        "/api/test/http/429",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=429,
        code="rate_limited",
        message="Too many requests. Try again later.",
    )
    assert response.headers["retry-after"] == "37"
    assert "x-internal-secret" not in response.headers
    assert "37" not in response.text


def test_unhandled_errors_are_redacted_from_response_and_logs(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = client.get(
        "/api/test/unhandled",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=500,
        code="internal_error",
        message="The server could not complete the request.",
    )
    assert REQUEST_ID in caplog.text
    assert SECRET not in caplog.text


def test_raw_error_responses_are_replaced_at_the_outer_boundary(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/test/raw-upstream",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=502,
        code="upstream_unavailable",
        message="An upstream service is unavailable.",
    )


@pytest.mark.parametrize(
    ("upstream_status", "public_status", "code", "message", "has_retry_after"),
    [
        (
            403,
            502,
            "upstream_unavailable",
            "An upstream service is unavailable.",
            False,
        ),
        (
            429,
            429,
            "rate_limited",
            "Too many requests. Try again later.",
            True,
        ),
        (
            503,
            502,
            "upstream_unavailable",
            "An upstream service is unavailable.",
            True,
        ),
        (504, 504, "upstream_timeout", "An upstream service timed out.", False),
    ],
)
def test_upstream_response_failures_are_safe_and_status_aware(
    client: TestClient,
    upstream_status: int,
    public_status: int,
    code: str,
    message: str,
    has_retry_after: bool,
) -> None:
    response = client.get(
        f"/api/test/upstream-response/{upstream_status}",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=public_status,
        code=code,
        message=message,
    )
    assert (response.headers.get("retry-after") == "19") is has_retry_after
    assert "x-upstream-secret" not in response.headers
    assert "19" not in response.text


@pytest.mark.parametrize(
    ("kind", "status_code", "code", "message"),
    [
        (
            "connect",
            502,
            "upstream_unavailable",
            "An upstream service is unavailable.",
        ),
        ("timeout", 504, "upstream_timeout", "An upstream service timed out."),
    ],
)
def test_upstream_transport_failures_distinguish_timeout_from_unavailable(
    client: TestClient,
    kind: str,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = client.get(
        f"/api/test/upstream-transport/{kind}",
        headers={"X-Request-ID": REQUEST_ID},
    )

    _assert_error(
        response,
        status_code=status_code,
        code=code,
        message=message,
    )


def test_non_error_responses_expose_the_preserved_request_id(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/test/success",
        headers={"X-Request-ID": REQUEST_ID},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"] == REQUEST_ID
    assert response.headers["cache-control"] == "no-store"


def test_invalid_or_ambiguous_request_id_is_replaced(client: TestClient) -> None:
    invalid = client.get(
        "/api/test/http/404",
        headers={"X-Request-ID": "not-a-uuid"},
    )
    duplicate = client.get(
        "/api/test/http/404",
        headers=[
            ("X-Request-ID", REQUEST_ID),
            ("X-Request-ID", "10000000-0000-4000-8000-000000000001"),
        ],
    )

    for response in (invalid, duplicate):
        generated = response.headers["x-request-id"]
        assert str(uuid.UUID(generated)) == generated
        assert generated not in {"not-a-uuid", REQUEST_ID}
        assert response.json()["request_id"] == generated


def test_outer_body_limit_errors_are_also_canonical() -> None:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=4)

    @app.post("/api/test/body")
    async def body(request: Request) -> None:
        await request.body()

    install_error_boundary(app)
    with TestClient(app, raise_server_exceptions=False) as limited_client:
        response = limited_client.post(
            "/api/test/body",
            headers={"X-Request-ID": REQUEST_ID},
            content=b"12345",
        )

    _assert_error(
        response,
        status_code=413,
        code="payload_too_large",
        message="The request body is too large.",
    )
