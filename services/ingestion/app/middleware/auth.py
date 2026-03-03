"""API key validation matching the C++ AuthMiddleware exactly.

Key format: proj_{project_id}_{secret}
  - project_id: alphanumeric, 1-64 characters
  - secret: alphanumeric, 16+ characters
"""

import re

_KEY_PATTERN = re.compile(r"^proj_([a-zA-Z0-9]{1,64})_([a-zA-Z0-9]{16,})$")


def extract_project_id(api_key: str) -> str | None:
    """Extract project_id from an API key, or return None if invalid."""
    if not api_key:
        return None
    match = _KEY_PATTERN.match(api_key)
    if not match:
        return None
    return match.group(1)
