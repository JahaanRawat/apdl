"""Focused tests for auxiliary LiteLLM completion options."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.editor.llm import resolve_completer


def _fake_litellm(calls: list[dict[str, object]]) -> SimpleNamespace:
    async def acompletion(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "choices": [{"message": {"content": " completed "}}],
        }

    return SimpleNamespace(acompletion=acompletion)


@pytest.mark.asyncio
async def test_resolve_completer_enforces_fixed_response_format(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(calls))
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "engineering_brief",
            "strict": True,
            "schema": {"type": "object", "additionalProperties": False},
        },
    }

    complete = resolve_completer(
        "xai/grok-4.5",
        timeout=12.5,
        api_key="test-key",
        response_format=response_format,
    )

    assert complete is not None
    assert await complete("system", "user") == "completed"
    assert calls == [
        {
            "model": "xai/grok-4.5",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "timeout": 12.5,
            "drop_params": False,
            "response_format": response_format,
            "api_key": "test-key",
        }
    ]


@pytest.mark.asyncio
async def test_resolve_completer_keeps_permissive_auxiliary_default(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(calls))

    complete = resolve_completer(
        "xai/grok-4.5",
        timeout=12.5,
        api_key="test-key",
    )

    assert complete is not None
    assert await complete("system", "user") == "completed"
    assert calls[0]["drop_params"] is True
    assert "response_format" not in calls[0]
