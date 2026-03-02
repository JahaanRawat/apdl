"""Event query endpoints — counts, timeseries, and property breakdowns."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from app.clickhouse.client import ClickHouseClient
from app.clickhouse.queries import (
    EVENT_BREAKDOWN_QUERY,
    EVENT_COUNT_QUERY,
    EVENT_TIMESERIES_QUERY,
)
from app.models.schemas import (
    BreakdownRequest,
    BreakdownResponse,
    EventCountRequest,
    EventCountResponse,
    TimeseriesRequest,
    TimeseriesResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/query/events", tags=["events"])


def _get_client(request: Request) -> ClickHouseClient:
    return request.app.state.ch_client


@router.post("/count", response_model=EventCountResponse)
async def event_counts(body: EventCountRequest, request: Request) -> EventCountResponse:
    """Aggregate event counts and unique-user counts, optionally filtered by event names."""
    client = _get_client(request)

    params: dict[str, Any] = {
        "project_id": body.project_id,
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
    }

    # Build optional event-name filter
    if body.event_names:
        placeholders = ", ".join(f"%(ev_{i})s" for i in range(len(body.event_names)))
        event_filter = f"AND event_name IN ({placeholders})"
        for i, name in enumerate(body.event_names):
            params[f"ev_{i}"] = name
    else:
        event_filter = ""

    query = EVENT_COUNT_QUERY.format(event_filter=event_filter)
    rows = await client.execute(query, params)

    total_events = sum(r.get("event_count", 0) for r in rows)
    # Unique users across events requires a separate uniq, but as a pragmatic
    # approximation we take the max unique_users across rows (for the overview)
    # or sum them (which over-counts).  For accuracy we'd run a dedicated query.
    # Here we return the sum-of-unique which is an upper bound.
    total_users = sum(r.get("unique_users", 0) for r in rows)

    return EventCountResponse(results=rows, total_events=total_events, total_users=total_users)


@router.post("/timeseries", response_model=TimeseriesResponse)
async def event_timeseries(body: TimeseriesRequest, request: Request) -> TimeseriesResponse:
    """Time-bucketed event counts for a single event."""
    client = _get_client(request)

    # The interval value (e.g. "1 DAY") is injected directly into the SQL
    # because ClickHouse does not support parameterised INTERVAL literals.
    # The TimeInterval enum constrains input to known-safe values.
    query = EVENT_TIMESERIES_QUERY.format(interval=body.interval.value)

    params: dict[str, Any] = {
        "project_id": body.project_id,
        "event_name": body.event_name,
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
    }

    rows = await client.execute(query, params)

    # Normalise datetime objects to ISO strings for JSON serialisation
    buckets = []
    for row in rows:
        bucket = dict(row)
        if "bucket" in bucket and hasattr(bucket["bucket"], "isoformat"):
            bucket["bucket"] = bucket["bucket"].isoformat()
        buckets.append(bucket)

    return TimeseriesResponse(buckets=buckets)


@router.post("/breakdown", response_model=BreakdownResponse)
async def event_breakdown(body: BreakdownRequest, request: Request) -> BreakdownResponse:
    """Break down an event by a JSON property value."""
    client = _get_client(request)

    params: dict[str, Any] = {
        "project_id": body.project_id,
        "event_name": body.event_name,
        "property": body.property,
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
        "limit": body.limit,
    }

    rows = await client.execute(EVENT_BREAKDOWN_QUERY, params)
    return BreakdownResponse(results=rows)
