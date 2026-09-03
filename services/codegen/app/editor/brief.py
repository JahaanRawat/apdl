"""Spec → engineering-brief compilation (the pre-edit auxiliary LLM pass).

Approved feature proposals arrive written at product altitude: they can demand
organizational actions ("stakeholder sign-off", "alert the data engineering
team") and infrastructure the connected repository does not have (ETL pipelines,
Slack webhooks). Handing that raw text to the editing agent forces it to guess a
repo-shaped interpretation mid-edit — the observed failure modes are fabricated
in-memory "pipelines", and near-empty diffs when the agent reads the unmet
dependencies as a reason to descope to nothing.

This pass does the interpretation *before* the edit, with the actual clone in
hand: it translates the spec into a work order grounded in this repository —
concrete files to touch, explicit descoping decisions for anything that cannot
be code here, and acceptance criteria a reviewer can check in the repo. The
brief replaces the spec in the agent's message; the original spec remains the
contract the post-edit review judges against.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
)

from app.editor.llm import CompleteFn
from app.inspection.repository import RepositoryInspector
from app.profiling import RepoProfile, profile_repository, render_profile

logger = logging.getLogger(__name__)

#: Path cap for the repo digest. Enough to show a real app's full shape; a
#: monorepo overflows and the digest says so rather than silently truncating.
_DIGEST_MAX_PATHS = 400
#: Directory names that never help the brief (dependencies, build output, VCS).
_DIGEST_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".venv",
        "venv",
        "__pycache__",
        "vendor",
        "target",
        ".turbo",
        "coverage",
    }
)
ENGINEERING_BRIEF_SCHEMA_VERSION = "engineering_brief@1"
_MAX_GOAL_CHARS = 2048
_MAX_LIST_ITEMS = 20
_MAX_LIST_ITEM_CHARS = 2000
_CANONICAL_SINGLE_LINE_PATTERN = (
    r"^[^\s\x00-\x1f\x7f-\x9f\u2028\u2029]"
    r"(?:[^\x00-\x1f\x7f-\x9f\u2028\u2029]*"
    r"[^\s\x00-\x1f\x7f-\x9f\u2028\u2029])?$"
)

_Goal = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=_MAX_GOAL_CHARS,
        pattern=_CANONICAL_SINGLE_LINE_PATTERN,
    ),
]
_BriefItem = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=_MAX_LIST_ITEM_CHARS,
        pattern=_CANONICAL_SINGLE_LINE_PATTERN,
    ),
]


class EngineeringBrief(BaseModel):
    """The one canonical model-generated engineering-brief contract."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["engineering_brief@1"]
    goal: _Goal
    scope_decisions: list[_BriefItem] = Field(
        min_length=1,
        max_length=_MAX_LIST_ITEMS,
    )
    implementation_plan: list[_BriefItem] = Field(
        min_length=1,
        max_length=_MAX_LIST_ITEMS,
    )
    acceptance_criteria: list[_BriefItem] = Field(
        min_length=1,
        max_length=_MAX_LIST_ITEMS,
    )

    @field_validator(
        "goal",
        "scope_decisions",
        "implementation_plan",
        "acceptance_criteria",
    )
    @classmethod
    def reject_noncanonical_text(
        cls,
        value: str | list[str],
    ) -> str | list[str]:
        """Keep rendering lossless: reject whitespace that would need cleanup."""
        values = [value] if isinstance(value, str) else value
        if any(not item.strip() for item in values):
            raise ValueError("engineering brief text cannot be blank")
        if any(item != item.strip() for item in values):
            raise ValueError("engineering brief text must not have edge whitespace")
        if any("\n" in item or "\r" in item for item in values):
            raise ValueError("engineering brief text must be single-line")
        if any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for item in values
            for character in item
        ):
            raise ValueError(
                "engineering brief text must not contain control, surrogate, "
                "or separator code points"
            )
        if not isinstance(value, str) and len(value) != len(set(value)):
            raise ValueError("engineering brief list items must be unique")
        return value


class EngineeringBriefParseError(ValueError):
    """Raised when helper output is not one exact canonical brief object."""


class EngineeringBriefCompilationStatus(StrEnum):
    """Application-level outcome after validating one helper response."""

    parsed = "parsed"
    unavailable = "unavailable"
    invalid = "invalid"


@dataclass(frozen=True)
class EngineeringBriefCompilation:
    """Typed result distinguishing provider absence from invalid output."""

    status: EngineeringBriefCompilationStatus
    markdown: str | None

    def __post_init__(self) -> None:
        if (self.status is EngineeringBriefCompilationStatus.parsed) != (
            self.markdown is not None
        ):
            raise ValueError("engineering brief compilation result is inconsistent")


BRIEF_SYSTEM = """\
You compile approved product feature proposals into precise engineering briefs
for an automated coding agent. The agent can ONLY edit files in the one
repository described below — it cannot contact people, configure external
services, or touch other systems. Your brief is the agent's entire understanding
of the task.

Return exactly one JSON object with these five required properties and no
others:

{
  "schema_version": "engineering_brief@1",
  "goal": "User-visible outcome in this repository.",
  "scope_decisions": ["One explicit scope decision."],
  "implementation_plan": ["One concrete, repository-grounded implementation step."],
  "acceptance_criteria": ["One observable acceptance criterion."]
}

Every value must use the exact type shown. All strings must be non-empty,
single-line text without leading or trailing whitespace. Each array must have
between 1 and 20 items. The goal may contain at most 2048 characters; every
array item may contain at most 2000 characters.

The goal is one paragraph describing the user-visible outcome this change
delivers in THIS repository.

For scope_decisions, rule explicitly on proposal items that cannot be code in
this repository (organizational actions, external infrastructure, human
sign-off): translate each into the closest in-repo equivalent or write an item
like "out of scope: X — requires Y". If all requested work is implementable,
include one item saying so. NEVER let an unimplementable item silently shrink
the implementable core; the agent must still build everything that CAN be built
here.

For implementation_plan, name concrete existing files to modify, new files to
create using repository conventions, and how each piece is wired into a
reachable route, page, layout, or registration. Name real files from the
repository digest; never invent paths for frameworks the repository does not
use.

For acceptance_criteria, provide checks a reviewer can verify by reading the
diff and running the repository's verification command. Every item must be
observable in this repository, never an organizational outcome.

Rules:
- Preserve the proposal's full implementable intent. The brief may narrow HOW,
  never quietly narrow WHAT.
- Stay within the repository's existing stack and dependencies.
- Output JSON only. Do not use Markdown fences, a preamble, commentary, or text
  after the JSON object.
"""

_BRIEF_CORRECTION = """\
# Required format correction

The previous response violated the engineering_brief@1 schema. Return a new
answer for the same repository and proposal. Output exactly one JSON object
matching the required schema, with no Markdown fence, preamble, or commentary.
Do not quote, summarize, or repair the previous response.
"""


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EngineeringBriefParseError(
                f"Engineering brief repeats JSON property {key!r}."
            )
        result[key] = value
    return result


def parse_engineering_brief(text: str) -> EngineeringBrief:
    """Parse one complete JSON object without fences or extraction fallbacks."""
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_object_keys)
    except EngineeringBriefParseError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise EngineeringBriefParseError(
            "Engineering brief response is not exact JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise EngineeringBriefParseError(
            "Engineering brief response must be one JSON object."
        )
    try:
        return EngineeringBrief.model_validate(payload)
    except ValidationError as exc:
        raise EngineeringBriefParseError(
            "Engineering brief response violates the strict schema."
        ) from exc


def render_engineering_brief(brief: EngineeringBrief) -> str:
    """Render a validated brief into the coding agent's canonical Markdown."""
    scope = "\n".join(f"- {item}" for item in brief.scope_decisions)
    plan = "\n".join(f"- {item}" for item in brief.implementation_plan)
    criteria = "\n".join(
        f"{index}. {item}"
        for index, item in enumerate(brief.acceptance_criteria, start=1)
    )
    return (
        f"## Goal\n{brief.goal}\n\n"
        f"## Scope decisions\n{scope}\n\n"
        f"## Implementation plan\n{plan}\n\n"
        f"## Acceptance criteria\n{criteria}"
    )


def engineering_brief_response_format() -> dict[str, object]:
    """Return the provider structured-output request for this exact contract."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "engineering_brief",
            "strict": True,
            "schema": EngineeringBrief.model_json_schema(),
        },
    }


def build_repo_digest(repo_dir: Path, profile: RepoProfile | None = None) -> str:
    """Canonical repository profile plus a bounded README excerpt."""
    contents = RepositoryInspector(repo_dir).text_view()
    sections = [
        "### Canonical repository profile\n"
        + render_profile(profile or profile_repository(repo_dir))
    ]

    for readme_name in ("README.md", "README.rst", "README"):
        inspected = contents.inspect(readme_name)
        if inspected is not None:
            head = inspected.text[:2000]
            sections.append(f"### README (head)\n{head}")
            break

    return "\n\n".join(sections)


def build_brief_user(
    *,
    title: str,
    spec: str,
    repo_digest: str,
    verification_context: str,
    correction: bool = False,
) -> str:
    """The exact user message the brief pass sends.

    Shared with the editor's prompt transcript (``EditResult.prompts``) so what
    the admin console shows is byte-for-byte what the model received.
    """
    prompt = (
        f"# Approved proposal\n\n## Title\n{title.strip()}\n\n"
        f"## Spec\n{spec.strip()}\n\n"
        f"# Repository digest\n\n{repo_digest.strip()}\n\n"
        f"# Repository verification\n\n{verification_context.strip()}"
    )
    return f"{prompt}\n\n{_BRIEF_CORRECTION}" if correction else prompt


async def compile_brief(
    *,
    title: str,
    spec: str,
    repo_digest: str,
    verification_context: str,
    complete: CompleteFn,
    correction: bool = False,
) -> EngineeringBriefCompilation:
    """Compile and validate one repo-grounded helper response.

    The caller owns retry and risk policy. Keeping this outcome typed prevents
    provider failure from being mislabeled as invalid model output.
    """
    user = build_brief_user(
        title=title,
        spec=spec,
        repo_digest=repo_digest,
        verification_context=verification_context,
        correction=correction,
    )
    response = await complete(BRIEF_SYSTEM, user)
    if response is None:
        logger.warning("Brief compilation returned no response for %r.", title)
        return EngineeringBriefCompilation(
            status=EngineeringBriefCompilationStatus.unavailable,
            markdown=None,
        )
    try:
        brief = parse_engineering_brief(response)
    except EngineeringBriefParseError:
        logger.warning("Brief compilation violated the strict schema for %r.", title)
        return EngineeringBriefCompilation(
            status=EngineeringBriefCompilationStatus.invalid,
            markdown=None,
        )
    return EngineeringBriefCompilation(
        status=EngineeringBriefCompilationStatus.parsed,
        markdown=render_engineering_brief(brief),
    )
