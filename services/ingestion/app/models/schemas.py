"""Pydantic models for API documentation.

These mirror the validation structures but are not used in the main request
flow -- manual validation (matching the C++ implementation) handles the
actual request processing.
"""

from pydantic import BaseModel


class ValidationError(BaseModel):
    field: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = []
