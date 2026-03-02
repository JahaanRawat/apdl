"""Pydantic request/response models for the Query Service."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class TimeInterval(str, Enum):
    """Supported time-bucket intervals for timeseries queries."""
    hour = "1 HOUR"
    day = "1 DAY"
    week = "1 WEEK"
    month = "1 MONTH"


class AnalysisMethod(str, Enum):
    """Statistical analysis method for experiment evaluation."""
    frequentist = "frequentist"
    bayesian = "bayesian"
    sequential = "sequential"


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------

class EventCountRequest(BaseModel):
    project_id: int
    start_date: date
    end_date: date
    event_names: list[str] | None = None


class EventCountResponse(BaseModel):
    results: list[dict[str, Any]]
    total_events: int
    total_users: int


class TimeseriesRequest(BaseModel):
    project_id: int
    event_name: str
    start_date: date
    end_date: date
    interval: TimeInterval = TimeInterval.day


class TimeseriesResponse(BaseModel):
    buckets: list[dict[str, Any]]


class BreakdownRequest(BaseModel):
    project_id: int
    event_name: str
    property: str
    start_date: date
    end_date: date
    limit: int = Field(default=20, le=100)


class BreakdownResponse(BaseModel):
    results: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Funnel models
# ---------------------------------------------------------------------------

class FunnelRequest(BaseModel):
    project_id: int
    steps: list[str]  # ordered event names
    start_date: date
    end_date: date
    window_days: int = Field(default=7, ge=1, le=90)


class FunnelStep(BaseModel):
    step: int
    event_name: str
    count: int
    conversion_rate: float  # from previous step (1.0 for step 1)
    overall_rate: float  # from step 1


class FunnelResponse(BaseModel):
    steps: list[FunnelStep]
    overall_conversion: float


# ---------------------------------------------------------------------------
# Retention models
# ---------------------------------------------------------------------------

class RetentionRequest(BaseModel):
    project_id: int
    cohort_event: str
    return_event: str
    start_date: date
    end_date: date
    period: str = "day"  # "day" or "week"


class RetentionCohort(BaseModel):
    cohort_date: str
    size: int
    retention: list[float]  # [day0_pct, day1_pct, ...]


class RetentionResponse(BaseModel):
    cohorts: list[RetentionCohort]


# ---------------------------------------------------------------------------
# Cohort comparison models
# ---------------------------------------------------------------------------

class CohortRequest(BaseModel):
    project_id: int
    cohort_property: str  # property to segment by
    metric_event: str
    start_date: date
    end_date: date


class CohortResponse(BaseModel):
    cohorts: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Experiment models
# ---------------------------------------------------------------------------

class ExperimentResultsRequest(BaseModel):
    experiment_id: str
    metric: str
    method: AnalysisMethod = AnalysisMethod.frequentist


class VariantResult(BaseModel):
    variant: str
    users: int
    mean: float
    stddev: float
    total: float


class ExperimentResult(BaseModel):
    experiment_id: str
    metric: str
    method: str
    variants: list[VariantResult]
    effect_size: float | None = None
    confidence_interval: tuple[float, float] | None = None
    p_value: float | None = None
    is_significant: bool
    recommendation: str
