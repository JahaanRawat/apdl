"""Bounded secret and personal-data redaction for observability previews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final


REDACTION_MARKER: Final = "[REDACTED]"
MAX_REDACTION_SCAN_BYTES: Final = 4 * 1024 * 1024
MAX_REDACTION_NODES: Final = 100_000


class RedactionLimitError(ValueError):
    """Untrusted content exceeded the bounded redaction work budget."""


@dataclass(frozen=True)
class RedactedValue:
    value: Any
    redacted: bool


_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:access[_-]?token|api[_-]?key|authorization|cookie|"
    r"client[_-]?secret|connection[_-]?string|database[_-]?url|id[_-]?token|"
    r"password|passphrase|private[_-]?key|refresh[_-]?token|secret|"
    r"session[_-]?token|anonymous[_-]?id|distinct[_-]?id|session[_-]?id|"
    r"user[_-]?id|email|phone|address|owners?|property[_-]?value|"
    r"cohort[_-]?value|values?|selectors?)(?:$|[_-])"
)

_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN [^\r\n-]*PRIVATE KEY(?: BLOCK)?-----.*?"
        r"(?:-----END [^\r\n-]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
        re.DOTALL,
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|"
        r"xai-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,})\b"
    ),
    re.compile(
        r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
        r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@[^\s]+"
    ),
    re.compile(
        r"(?i)(?:[?&]|\b)(?:access_token|api[_-]?key|client_secret|"
        r"password|refresh_token|secret|token)=([^&#\s\"']{4,})"
    ),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(
        r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
    ),
)


def _redact_text(value: str) -> tuple[str, bool]:
    redacted = False
    for pattern in _TEXT_PATTERNS:
        value, count = pattern.subn(REDACTION_MARKER, value)
        redacted = redacted or count > 0
    return value, redacted


def redact_json_value(
    value: Any,
    *,
    max_text_bytes: int = MAX_REDACTION_SCAN_BYTES,
    max_nodes: int = MAX_REDACTION_NODES,
) -> RedactedValue:
    """Return a redacted JSON-shaped copy within explicit work ceilings."""
    if type(max_text_bytes) is not int or max_text_bytes <= 0:
        raise ValueError("max_text_bytes must be a positive integer")
    if type(max_nodes) is not int or max_nodes <= 0:
        raise ValueError("max_nodes must be a positive integer")

    nodes = 0
    text_bytes = 0

    def visit(item: Any) -> tuple[Any, bool]:
        nonlocal nodes, text_bytes
        nodes += 1
        if nodes > max_nodes:
            raise RedactionLimitError("tool result exceeds the redaction node limit")
        if isinstance(item, str):
            text_bytes += len(item.encode("utf-8"))
            if text_bytes > max_text_bytes:
                raise RedactionLimitError("tool result exceeds the redaction byte limit")
            return _redact_text(item)
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            changed = False
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError("tool result mappings require string keys")
                text_bytes += len(key.encode("utf-8"))
                if text_bytes > max_text_bytes:
                    raise RedactionLimitError(
                        "tool result exceeds the redaction byte limit"
                    )
                if _SENSITIVE_KEY.search(key):
                    result[key] = REDACTION_MARKER
                    changed = True
                    continue
                redacted_child, child_changed = visit(child)
                result[key] = redacted_child
                changed = changed or child_changed
            return result, changed
        if isinstance(item, list):
            result_list: list[Any] = []
            changed = False
            for child in item:
                redacted_child, child_changed = visit(child)
                result_list.append(redacted_child)
                changed = changed or child_changed
            return result_list, changed
        if item is None or type(item) in {bool, int, float}:
            return item, False
        raise TypeError("tool result must normalize to a JSON-shaped value")

    redacted_value, changed = visit(value)
    return RedactedValue(redacted_value, changed)
