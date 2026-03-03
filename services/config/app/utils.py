"""Shared utilities for the config service."""

import re

from fastapi import Request

_KEY_PATTERN = re.compile(r"^proj_([a-zA-Z0-9]{1,64})_([a-zA-Z0-9]{16,})$")


def extract_project_id(request: Request) -> str:
    """Extract the project_id from the API key header, query params, or direct param.

    Checks in order:
    1. X-API-Key header  (format: proj_{project_id}_{secret})
    2. api_key query parameter (same format)
    3. project_id query parameter (raw project ID)
    """
    api_key = request.headers.get("x-api-key") or request.query_params.get(
        "api_key", ""
    )
    m = _KEY_PATTERN.match(api_key)
    if m:
        return m.group(1)
    return request.query_params.get("project_id", "")
