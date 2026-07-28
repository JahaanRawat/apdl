"""Execute compiled event selectors on the exact shipped ClickHouse engine."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from app.clickhouse.client import ClickHouseClient
from app.clickhouse.selectors import build_selector_condition
from app.models.schemas import MAX_EXACT_FILTER_INTEGER, EventSelector


def _selector(operator: str, value: Any = None) -> EventSelector:
    filter_: dict[str, Any] = {
        "property": "subject",
        "operator": operator,
    }
    if operator not in {"exists", "not_exists"}:
        filter_["value"] = value
    return EventSelector.model_validate(
        {
            "event_name": "selector_matrix",
            "filters": [filter_],
        }
    )


@asynccontextmanager
async def _exact_clickhouse_client() -> AsyncIterator[ClickHouseClient]:
    host = os.getenv("APDL_TEST_CLICKHOUSE_HOST")
    if not host:
        pytest.skip(
            "set APDL_TEST_CLICKHOUSE_HOST to execute selectors on pinned ClickHouse"
        )

    environment = {
        "CLICKHOUSE_HOST": host,
        "CLICKHOUSE_PORT": os.getenv("APDL_TEST_CLICKHOUSE_PORT", "9000"),
        "CLICKHOUSE_USER": os.getenv("APDL_TEST_CLICKHOUSE_USER", "apdl"),
        "CLICKHOUSE_PASSWORD": os.getenv(
            "APDL_TEST_CLICKHOUSE_PASSWORD",
            "apdl_dev",
        ),
        "CLICKHOUSE_DB": os.getenv("APDL_TEST_CLICKHOUSE_DB", "apdl"),
        "CLICKHOUSE_POOL_SIZE": "1",
    }
    with patch.dict(os.environ, environment):
        client = ClickHouseClient()
        await client.connect()
        try:
            yield client
        finally:
            await client.close()


async def _selector_matches(
    client: ClickHouseClient,
    selector: EventSelector,
    properties: dict[str, Any],
) -> bool:
    params: dict[str, Any] = {
        "row_event_name": selector.event_name,
        "row_properties": json.dumps(properties, separators=(",", ":")),
    }
    condition = build_selector_condition(selector, params, "exact")
    query = f"""
SELECT toUInt8({condition}) AS matched
FROM
(
    SELECT
        %(row_event_name)s AS event_name,
        %(row_properties)s AS properties
)
"""
    rows = await client.execute(query, params)
    assert len(rows) == 1
    return rows[0]["matched"] == 1


async def _property_json_type(
    client: ClickHouseClient,
    properties: dict[str, Any],
) -> str:
    rows = await client.execute(
        """
SELECT JSONType(%(row_properties)s, 'subject') AS json_type
""",
        {
            "row_properties": json.dumps(properties, separators=(",", ":")),
        },
    )
    assert len(rows) == 1
    return rows[0]["json_type"]


@pytest.mark.asyncio
async def test_every_accepted_selector_operator_executes_on_pinned_clickhouse():
    cases = [
        ("eq", "pro", {"subject": "pro"}),
        ("neq", "free", {"subject": "pro"}),
        ("in", ["pro", "team"], {"subject": "team"}),
        ("not_in", ["free", "starter"], {"subject": "team"}),
        ("eq", True, {"subject": True}),
        ("neq", False, {"subject": True}),
        ("in", [True], {"subject": True}),
        ("not_in", [False], {"subject": True}),
        ("in", [5], {"subject": 5}),
        ("not_in", [4], {"subject": 5}),
        ("exists", None, {"subject": "present"}),
        ("not_exists", None, {"different": "present"}),
        ("contains", "Start", {"subject": "Start checkout"}),
        ("gt", 4, {"subject": 5}),
        ("gte", 5, {"subject": 5}),
        ("lt", 6, {"subject": 5}),
        ("lte", 5, {"subject": 5}),
    ]

    async with _exact_clickhouse_client() as client:
        for operator, value, properties in cases:
            assert await _selector_matches(
                client,
                _selector(operator, value),
                properties,
            ), operator


@pytest.mark.asyncio
async def test_double_and_safe_int64_selectors_execute_on_pinned_clickhouse():
    safe_int64_value = MAX_EXACT_FILTER_INTEGER
    cases = [
        ("eq", 5.25, {"subject": 5.25}, "Double"),
        ("in", [5.25], {"subject": 5.25}, "Double"),
        ("not_in", [4.5], {"subject": 5.25}, "Double"),
        ("eq", 5, {"subject": 5.0}, "Double"),
        ("eq", safe_int64_value, {"subject": safe_int64_value}, "Int64"),
        ("in", [safe_int64_value], {"subject": safe_int64_value}, "Int64"),
        ("not_in", [safe_int64_value - 1], {"subject": safe_int64_value}, "Int64"),
    ]

    async with _exact_clickhouse_client() as client:
        for operator, value, properties, expected_type in cases:
            assert await _property_json_type(client, properties) == expected_type
            assert await _selector_matches(
                client,
                _selector(operator, value),
                properties,
            ), (operator, expected_type)


@pytest.mark.asyncio
async def test_out_of_range_integers_never_match_numeric_selectors():
    cases = [
        ({"subject": MAX_EXACT_FILTER_INTEGER + 1}, "Int64"),
        ({"subject": -(MAX_EXACT_FILTER_INTEGER + 1)}, "Int64"),
        ({"subject": 2**63}, "UInt64"),
    ]
    operators = [
        ("eq", MAX_EXACT_FILTER_INTEGER),
        ("neq", MAX_EXACT_FILTER_INTEGER),
        ("in", [MAX_EXACT_FILTER_INTEGER]),
        ("not_in", [MAX_EXACT_FILTER_INTEGER]),
    ]

    async with _exact_clickhouse_client() as client:
        for properties, expected_type in cases:
            assert await _property_json_type(client, properties) == expected_type
            for operator, value in operators:
                assert not await _selector_matches(
                    client,
                    _selector(operator, value),
                    properties,
                ), (operator, expected_type)


@pytest.mark.asyncio
async def test_explicit_json_null_exists_but_never_matches_typed_filters():
    typed_filters = [
        ("eq", "value"),
        ("neq", "value"),
        ("in", ["value"]),
        ("not_in", ["value"]),
        ("contains", "value"),
        ("gt", 0),
        ("gte", 0),
        ("lt", 1),
        ("lte", 1),
    ]

    async with _exact_clickhouse_client() as client:
        properties = {"subject": None}
        assert await _selector_matches(client, _selector("exists"), properties)
        assert not await _selector_matches(
            client,
            _selector("not_exists"),
            properties,
        )
        for operator, value in typed_filters:
            assert not await _selector_matches(
                client,
                _selector(operator, value),
                properties,
            ), operator


@pytest.mark.asyncio
async def test_contains_is_case_sensitive_on_pinned_clickhouse():
    async with _exact_clickhouse_client() as client:
        selector = _selector("contains", "Start")
        assert await _selector_matches(
            client,
            selector,
            {"subject": "Start checkout"},
        )
        assert not await _selector_matches(
            client,
            selector,
            {"subject": "start checkout"},
        )


@pytest.mark.asyncio
async def test_typed_selectors_reject_cross_type_values_on_pinned_clickhouse():
    cases = [
        ("eq", "5", {"subject": 5}),
        ("eq", 1, {"subject": "1"}),
        ("eq", True, {"subject": 1}),
        ("neq", "different", {"subject": 5}),
        ("neq", 2, {"subject": "1"}),
        ("neq", False, {"subject": 1}),
        ("in", ["5"], {"subject": 5}),
        ("in", [1], {"subject": "1"}),
        ("in", [True], {"subject": 1}),
        ("not_in", ["different"], {"subject": 5}),
        ("not_in", [2], {"subject": "1"}),
        ("not_in", [False], {"subject": 1}),
        ("contains", "5", {"subject": 5}),
        ("contains", "true", {"subject": True}),
        ("contains", "Start", {"subject": "start checkout"}),
        ("gt", 4, {"subject": "5"}),
        ("gt", 0, {"subject": True}),
        ("gte", 5, {"subject": "5"}),
        ("lt", 6, {"subject": "5"}),
        ("lte", 5, {"subject": "5"}),
    ]

    async with _exact_clickhouse_client() as client:
        for operator, value, properties in cases:
            assert not await _selector_matches(
                client,
                _selector(operator, value),
                properties,
            ), (operator, value, properties)
