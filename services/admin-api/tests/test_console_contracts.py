from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import FormatChecker, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "contracts" / "console" / "v1"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
OPENAPI_PATH = CONTRACT_ROOT / "openapi.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


SCHEMAS = {
    path.name: _load_json(path) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))
}
REGISTRY = Registry()
for _schema in SCHEMAS.values():
    REGISTRY = REGISTRY.with_resource(
        _schema["$id"],
        Resource.from_contents(_schema),
    )


VALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "console-capabilities.schema.json": {
        "schema_version": "console_capabilities@1",
        "registration_enabled": True,
    },
    "console-manifest.schema.json": {
        "schema_version": "console_manifest@1",
        "deployment_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
        "display_name": "Local APDL",
        "backend_version": "0.3.4",
        "build_revision": "a9a347ce2ae5f2432767f4fdfe563f7d0ea8970f",
        "console_api_version": 1,
    },
    "console-login-request.schema.json": {
        "email": "user@example.com",
        "password": "correct horse battery staple",
    },
    "console-registration-request.schema.json": {
        "email": "new-user@example.com",
        "password": "correct horse battery staple",
    },
    "console-session.schema.json": {
        "schema_version": "console_session@1",
        "access_token": "A" * 43,
        "expires_at": "2026-08-13T21:00:00Z",
    },
    "console-identity.schema.json": {
        "schema_version": "console_identity@1",
        "user_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
        "email": "user@example.com",
        "projects": [
            {
                "project_id": "demo",
                "roles": ["config:read", "query:read"],
            }
        ],
    },
    "error.schema.json": {
        "schema_version": "error@1",
        "code": "invalid_credentials",
        "message": "Invalid email or password",
        "request_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
    },
    "console-stream-control.schema.json": {
        "schema_version": "console_stream_control@1",
        "code": "project_access_revoked",
        "message": "Project access was revoked",
        "project_id": "demo",
        "required_role": "config:read",
    },
}


def _validator(name: str):
    schema = SCHEMAS[name]
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(
        schema,
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )


def _object_schemas(value: Any):
    if isinstance(value, dict):
        if value.get("type") == "object":
            yield value
        for item in value.values():
            yield from _object_schemas(item)
    elif isinstance(value, list):
        for item in value:
            yield from _object_schemas(item)


def test_contract_schema_set_is_explicit_and_meta_valid() -> None:
    assert set(SCHEMAS) == {
        "common.schema.json",
        "console-capabilities.schema.json",
        "console-identity.schema.json",
        "console-login-request.schema.json",
        "console-manifest.schema.json",
        "console-registration-request.schema.json",
        "console-session.schema.json",
        "console-stream-control.schema.json",
        "error.schema.json",
    }
    for name, schema in SCHEMAS.items():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (
            f"https://apdl.dev/contracts/console/v1/schemas/{name}"
        )
        validator_for(schema).check_schema(schema)


def test_every_contract_object_rejects_unknown_fields() -> None:
    objects = [
        item
        for schema in SCHEMAS.values()
        for item in _object_schemas(schema)
    ]
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)

    for name, payload in VALID_PAYLOADS.items():
        _validator(name).validate(payload)
        with pytest.raises(ValidationError):
            _validator(name).validate({**payload, "unknown_field": True})


def test_versioned_contracts_require_the_exact_declared_version() -> None:
    for name, payload in VALID_PAYLOADS.items():
        if "schema_version" not in payload:
            continue
        incompatible = copy.deepcopy(payload)
        incompatible["schema_version"] = f"{payload['schema_version']}-fallback"
        with pytest.raises(ValidationError):
            _validator(name).validate(incompatible)

    manifest = copy.deepcopy(VALID_PAYLOADS["console-manifest.schema.json"])
    manifest["console_api_version"] = 2
    with pytest.raises(ValidationError):
        _validator("console-manifest.schema.json").validate(manifest)


@pytest.mark.parametrize(
    "schema_name",
    [
        "console-login-request.schema.json",
        "console-registration-request.schema.json",
    ],
)
def test_email_contract_matches_runtime_address_shape(schema_name: str) -> None:
    payload = copy.deepcopy(VALID_PAYLOADS[schema_name])
    payload["email"] = "user@localhost"

    with pytest.raises(ValidationError):
        _validator(schema_name).validate(payload)


def test_identity_and_stream_control_use_canonical_project_roles() -> None:
    identity = copy.deepcopy(VALID_PAYLOADS["console-identity.schema.json"])
    identity["projects"][0]["roles"] = ["admin"]
    with pytest.raises(ValidationError):
        _validator("console-identity.schema.json").validate(identity)

    stream = copy.deepcopy(VALID_PAYLOADS["console-stream-control.schema.json"])
    stream["required_role"] = "admin"
    with pytest.raises(ValidationError):
        _validator("console-stream-control.schema.json").validate(stream)

    stream["required_role"] = "config:read"
    stream["code"] = "retry_with_cookie"
    with pytest.raises(ValidationError):
        _validator("console-stream-control.schema.json").validate(stream)


def test_openapi_references_the_canonical_schema_documents() -> None:
    document = _load_json(OPENAPI_PATH)
    assert document["openapi"] == "3.1.0"
    assert set(document["paths"]) == {
        "/api/console/v1/capabilities",
        "/api/console/v1/manifest",
        "/api/console/v1/registrations",
        "/api/console/v1/session",
        "/api/console/v1/sessions",
        "/api/projects/{project_id}/config/v1/stream",
    }
    assert document["components"]["securitySchemes"] == {
        "consoleBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
        }
    }
    assert set(document["components"]["schemas"]) == {
        "ConsoleCapabilities",
        "ConsoleManifest",
        "ConsoleLoginRequest",
        "ConsoleRegistrationRequest",
        "ConsoleSession",
        "ConsoleIdentity",
        "ConsoleError",
        "ConsoleStreamControl",
    }
    for component in document["components"]["schemas"].values():
        reference = component["$ref"]
        assert reference.startswith("schemas/")
        assert (CONTRACT_ROOT / reference).is_file()


def test_openapi_declares_public_and_bearer_operations_without_aliases() -> None:
    document = _load_json(OPENAPI_PATH)
    paths = document["paths"]
    assert paths["/api/console/v1/capabilities"]["get"]["security"] == []
    assert paths["/api/console/v1/manifest"]["get"]["security"] == []
    assert paths["/api/console/v1/registrations"]["post"]["security"] == []
    assert paths["/api/console/v1/sessions"]["post"]["security"] == []
    assert paths["/api/console/v1/session"]["get"]["security"] == [
        {"consoleBearer": []}
    ]
    assert paths["/api/console/v1/session"]["delete"]["security"] == [
        {"consoleBearer": []}
    ]
    assert paths["/api/projects/{project_id}/config/v1/stream"]["get"][
        "security"
    ] == [{"consoleBearer": []}]
    serialized = json.dumps(document, sort_keys=True)
    for forbidden in (
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/register",
        "X-CSRF-Token",
        "cookieAuth",
    ):
        assert forbidden not in serialized


def test_openapi_declares_request_identity_and_header_only_retry_guidance() -> None:
    document = _load_json(OPENAPI_PATH)
    request_id = {"$ref": "#/components/headers/RequestId"}
    paths = document["paths"]
    successful_responses = (
        paths["/api/console/v1/capabilities"]["get"]["responses"]["200"],
        paths["/api/console/v1/manifest"]["get"]["responses"]["200"],
        paths["/api/console/v1/registrations"]["post"]["responses"]["201"],
        paths["/api/console/v1/sessions"]["post"]["responses"]["200"],
        paths["/api/console/v1/session"]["get"]["responses"]["200"],
        paths["/api/console/v1/session"]["delete"]["responses"]["204"],
        paths["/api/projects/{project_id}/config/v1/stream"]["get"][
            "responses"
        ]["200"],
    )
    for response in successful_responses:
        assert response["headers"]["X-Request-ID"] == request_id

    error_response = document["components"]["responses"]["ConsoleError"]
    assert error_response["headers"]["X-Request-ID"] == request_id
    assert error_response["headers"]["Cache-Control"]["schema"]["const"] == (
        "no-store"
    )
    assert "Retry-After" in error_response["headers"]
    assert set(SCHEMAS["error.schema.json"]["properties"]) == {
        "schema_version",
        "code",
        "message",
        "request_id",
    }
