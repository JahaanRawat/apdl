"""LangGraph tool definitions for ClickHouse queries via the Query Service HTTP API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.tools import tool

QUERY_SERVICE_URL = os.getenv("QUERY_SERVICE_URL", "http://localhost:8082")
_TIMEOUT = 30.0


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the query service and return the JSON response."""
    async with httpx.AsyncClient(base_url=QUERY_SERVICE_URL, timeout=_TIMEOUT) as client:
        resp = await client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()


@tool
async def query_events(
    project_id: int,
    start_date: str,
    end_date: str,
    event_names: list[str] | None = None,
) -> dict[str, Any]:
    """Query aggregated event counts and unique users.

    Args:
        project_id: The project to query.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        event_names: Optional list of event names to filter.
    """
    payload: dict[str, Any] = {
        "project_id": project_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if event_names:
        payload["event_names"] = event_names
    return await _post("/v1/query/events/count", payload)


@tool
async def query_timeseries(
    project_id: int,
    event_name: str,
    start_date: str,
    end_date: str,
    interval: str = "1 DAY",
) -> dict[str, Any]:
    """Query time-bucketed event counts for a single event.

    Args:
        project_id: The project to query.
        event_name: The event to chart.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        interval: Bucket interval — "1 HOUR", "1 DAY", "1 WEEK", or "1 MONTH".
    """
    return await _post("/v1/query/events/timeseries", {
        "project_id": project_id,
        "event_name": event_name,
        "start_date": start_date,
        "end_date": end_date,
        "interval": interval,
    })


@tool
async def query_funnel(
    project_id: int,
    steps: list[str],
    start_date: str,
    end_date: str,
    window_days: int = 7,
) -> dict[str, Any]:
    """Run a multi-step funnel analysis.

    Args:
        project_id: The project to query.
        steps: Ordered list of event names defining the funnel.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        window_days: Max days between first and last step.
    """
    return await _post("/v1/query/funnel", {
        "project_id": project_id,
        "steps": steps,
        "start_date": start_date,
        "end_date": end_date,
        "window_days": window_days,
    })


@tool
async def query_retention(
    project_id: int,
    cohort_event: str,
    return_event: str,
    start_date: str,
    end_date: str,
    period: str = "day",
) -> dict[str, Any]:
    """Compute an N-day or N-week retention grid.

    Args:
        project_id: The project to query.
        cohort_event: Event that defines the cohort (first occurrence).
        return_event: Event to check for retention.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        period: "day" or "week".
    """
    return await _post("/v1/query/retention", {
        "project_id": project_id,
        "cohort_event": cohort_event,
        "return_event": return_event,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
    })


@tool
async def query_cohort(
    project_id: int,
    cohort_property: str,
    metric_event: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Compare metrics across user cohorts defined by a property value.

    Args:
        project_id: The project to query.
        cohort_property: JSON property to segment users by.
        metric_event: Event to measure across cohorts.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
    """
    return await _post("/v1/query/cohort", {
        "project_id": project_id,
        "cohort_property": cohort_property,
        "metric_event": metric_event,
        "start_date": start_date,
        "end_date": end_date,
    })


@tool
async def query_breakdown(
    project_id: int,
    event_name: str,
    property_name: str,
    start_date: str,
    end_date: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Break down an event by a JSON property value.

    Args:
        project_id: The project to query.
        event_name: Event to analyse.
        property_name: JSON property key to break down by.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        limit: Max number of distinct values to return.
    """
    return await _post("/v1/query/events/breakdown", {
        "project_id": project_id,
        "event_name": event_name,
        "property": property_name,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
    })
