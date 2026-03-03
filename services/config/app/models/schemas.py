"""Pydantic models for flags and experiments."""

from pydantic import BaseModel


class FlagConfig(BaseModel):
    key: str
    project_id: str = ""
    enabled: bool = False
    description: str = ""
    variant_type: str = "boolean"
    default_value: str = "false"
    rules_json: str = "[]"
    variants_json: str = "[]"
    rollout_percentage: float = 100.0
    created_at: str = ""
    updated_at: str = ""


class ExperimentConfig(BaseModel):
    key: str
    project_id: str = ""
    status: str = "draft"
    description: str = ""
    variants_json: str = "[]"
    targeting_rules_json: str = "[]"
    traffic_percentage: float = 100.0
    start_date: str = ""
    end_date: str = ""
    created_at: str = ""
    updated_at: str = ""


class EvalContext(BaseModel):
    user_id: str = ""
    anonymous_id: str = ""
    attributes: dict[str, str] = {}


class EvalResult(BaseModel):
    key: str
    enabled: bool = False
    value: str = ""
    variant: str = ""
    reason: str = ""
