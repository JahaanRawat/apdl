"""Public compatibility surface for the separately deployed APDL Console."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.config import FULL_GIT_REVISION_PATTERN, SEMVER_PATTERN

router = APIRouter(prefix="/api/console/v1", tags=["console"])

UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
DISPLAY_NAME_PATTERN = r"^[^\x00-\x1f\x7f]+$"


class ConsoleManifest(BaseModel):
    """Exact version-one connection manifest returned before authentication."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["console_manifest@1"] = "console_manifest@1"
    deployment_id: str = Field(pattern=UUID_PATTERN)
    display_name: str = Field(
        min_length=1,
        max_length=100,
        pattern=DISPLAY_NAME_PATTERN,
    )
    backend_version: str = Field(pattern=SEMVER_PATTERN.pattern)
    build_revision: str = Field(pattern=FULL_GIT_REVISION_PATTERN.pattern)
    console_api_version: Literal[1] = 1


class ConsoleCapabilities(BaseModel):
    """Public feature availability for this deployment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["console_capabilities@1"] = "console_capabilities@1"
    registration_enabled: bool


@router.get("/manifest", response_model=ConsoleManifest)
async def get_console_manifest(request: Request, response: Response) -> ConsoleManifest:
    """Return public compatibility metadata without consulting protected state."""
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    return ConsoleManifest(
        deployment_id=settings.deployment_id,
        display_name=settings.display_name,
        backend_version=settings.backend_version,
        build_revision=settings.build_revision,
    )


@router.get("/capabilities", response_model=ConsoleCapabilities)
async def get_console_capabilities(
    request: Request,
    response: Response,
) -> ConsoleCapabilities:
    """Return public deployment features without consulting protected state."""
    response.headers["Cache-Control"] = "no-store"
    return ConsoleCapabilities(
        registration_enabled=request.app.state.settings.registration_enabled
    )
