from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import console
from conftest import make_settings

DEPLOYMENT_ID = "87fab7d6-dba0-4f77-8ffd-00e815fc7303"
BUILD_REVISION = "a" * 40


def manifest_client() -> TestClient:
    app = FastAPI(redirect_slashes=False)
    app.state.settings = make_settings(
        deployment_id=DEPLOYMENT_ID,
        display_name="Test APDL",
        backend_version="0.3.4",
        build_revision=BUILD_REVISION,
    )
    app.include_router(console.router)
    return TestClient(app)


def test_manifest_is_public_exact_and_uncacheable() -> None:
    with manifest_client() as client:
        response = client.get("/api/console/v1/manifest")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert response.json() == {
        "schema_version": "console_manifest@1",
        "deployment_id": DEPLOYMENT_ID,
        "display_name": "Test APDL",
        "backend_version": "0.3.4",
        "build_revision": BUILD_REVISION,
        "console_api_version": 1,
    }


def test_manifest_route_does_not_redirect_or_accept_an_alternate_method() -> None:
    with manifest_client() as client:
        trailing_slash = client.get(
            "/api/console/v1/manifest/",
            follow_redirects=False,
        )
        post = client.post("/api/console/v1/manifest", follow_redirects=False)

    assert trailing_slash.status_code == 404
    assert "location" not in trailing_slash.headers
    assert post.status_code == 405
    assert "location" not in post.headers


def test_manifest_model_forbids_unknown_fields() -> None:
    payload = {
        "deployment_id": DEPLOYMENT_ID,
        "display_name": "Test APDL",
        "backend_version": "0.3.4",
        "build_revision": BUILD_REVISION,
        "unexpected": True,
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        console.ConsoleManifest.model_validate(payload, strict=True)
