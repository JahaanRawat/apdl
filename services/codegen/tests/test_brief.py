"""Tests for the pre-edit brief compilation (spec → repo-grounded work order)."""

import json
from copy import deepcopy

import pytest

from app.editor.brief import (
    BRIEF_SYSTEM,
    ENGINEERING_BRIEF_SCHEMA_VERSION,
    EngineeringBrief,
    EngineeringBriefCompilationStatus,
    EngineeringBriefParseError,
    build_brief_user,
    build_repo_digest,
    compile_brief,
    engineering_brief_response_format,
    parse_engineering_brief,
    render_engineering_brief,
)
from app.inspection.repository import InspectionPathError

VALID_PAYLOAD = {
    "schema_version": ENGINEERING_BRIEF_SCHEMA_VERSION,
    "goal": "Deliver the thing.",
    "scope_decisions": [
        "out of scope: Slack alerting — repository has no Slack wiring",
        "Keep the complete in-repository UI change in scope.",
    ],
    "implementation_plan": [
        "Modify app/page.tsx to add the reachable user interface.",
        "Update app/page.test.tsx with behavior coverage.",
    ],
    "acceptance_criteria": [
        "The route renders the new interface.",
        "The repository verification command passes.",
    ],
}
VALID_RESPONSE = json.dumps(VALID_PAYLOAD)
RENDERED_BRIEF = (
    "## Goal\nDeliver the thing.\n\n"
    "## Scope decisions\n"
    "- out of scope: Slack alerting — repository has no Slack wiring\n"
    "- Keep the complete in-repository UI change in scope.\n\n"
    "## Implementation plan\n"
    "- Modify app/page.tsx to add the reachable user interface.\n"
    "- Update app/page.test.tsx with behavior coverage.\n\n"
    "## Acceptance criteria\n"
    "1. The route renders the new interface.\n"
    "2. The repository verification command passes."
)


def _make_complete(reply):
    calls = []

    async def complete(system: str, user: str):
        calls.append((system, user))
        return reply

    return complete, calls


def test_digest_lists_files_and_excludes_noise_dirs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "page.tsx").write_text("x")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")

    digest = build_repo_digest(tmp_path)

    assert "app/page.tsx" in digest
    assert "node_modules" not in digest
    assert ".git" not in digest


def test_digest_includes_scripts_dependencies_and_readme(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "next build"},
                "dependencies": {"next": "^15"},
                "devDependencies": {"typescript": "^5"},
            }
        )
    )
    (tmp_path / "README.md").write_text("# Demo app\nA fake fintech site.")

    digest = build_repo_digest(tmp_path)

    assert "npm run build" in digest
    assert '"name": "next"' in digest
    assert '"name": "typescript"' in digest
    assert "A fake fintech site." in digest


def test_digest_marks_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.profiling.profiler._MAX_PATHS", 2)
    for name in ("a.ts", "b.ts", "c.ts"):
        (tmp_path / name).write_text("x")

    digest = build_repo_digest(tmp_path)

    assert "truncated" in digest
    assert "c.ts" not in digest


def test_digest_rejects_proc_like_readme_symlink(tmp_path):
    outside = tmp_path.parent / "proc-like-secret"
    outside.write_text(
        "OPENAI_API_KEY=provider-secret-that-must-not-enter-the-digest\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").symlink_to(outside)

    with pytest.raises(
        InspectionPathError, match="repository contains a symbolic link"
    ):
        build_repo_digest(tmp_path)


def test_parse_and_render_engineering_brief_are_canonical():
    parsed = parse_engineering_brief(VALID_RESPONSE)

    assert parsed.schema_version == ENGINEERING_BRIEF_SCHEMA_VERSION
    assert render_engineering_brief(parsed) == RENDERED_BRIEF


def test_engineering_brief_json_schema_is_closed_required_and_bounded():
    schema = EngineeringBrief.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "schema_version",
        "goal",
        "scope_decisions",
        "implementation_plan",
        "acceptance_criteria",
    ]
    assert schema["properties"]["schema_version"]["const"] == (
        ENGINEERING_BRIEF_SCHEMA_VERSION
    )
    assert schema["properties"]["goal"]["minLength"] == 1
    assert schema["properties"]["goal"]["maxLength"] == 2048
    expected_pattern = (
        r"^[^\s\x00-\x1f\x7f-\x9f\u2028\u2029]"
        r"(?:[^\x00-\x1f\x7f-\x9f\u2028\u2029]*"
        r"[^\s\x00-\x1f\x7f-\x9f\u2028\u2029])?$"
    )
    assert schema["properties"]["goal"]["pattern"] == expected_pattern
    for name in ("scope_decisions", "implementation_plan", "acceptance_criteria"):
        field = schema["properties"][name]
        assert field["minItems"] == 1
        assert field["maxItems"] == 20
        assert field["items"]["minLength"] == 1
        assert field["items"]["maxLength"] == 2000
        assert field["items"]["pattern"] == expected_pattern


def test_engineering_brief_response_format_enforces_the_canonical_schema():
    response_format = engineering_brief_response_format()

    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "engineering_brief",
            "strict": True,
            "schema": EngineeringBrief.model_json_schema(),
        },
    }


@pytest.mark.parametrize(
    "response",
    [
        "```json\n" + VALID_RESPONSE + "\n```",
        "Here is the brief:\n" + VALID_RESPONSE,
        VALID_RESPONSE + "\nDone.",
        json.dumps([VALID_PAYLOAD]),
        (
            '{"schema_version":"engineering_brief@1",'
            '"schema_version":"engineering_brief@1",'
            '"goal":"Goal","scope_decisions":["None."],'
            '"implementation_plan":["Edit app.py."],'
            '"acceptance_criteria":["Tests pass."]}'
        ),
    ],
)
def test_parse_rejects_non_exact_json(response):
    with pytest.raises(EngineeringBriefParseError):
        parse_engineering_brief(response)


def _payload_with(**updates):
    payload = deepcopy(VALID_PAYLOAD)
    payload.update(updates)
    return json.dumps(payload)


@pytest.mark.parametrize(
    "response",
    [
        json.dumps(
            {key: value for key, value in VALID_PAYLOAD.items() if key != "goal"}
        ),
        _payload_with(schema_version="engineering_brief@2"),
        _payload_with(goal=7),
        _payload_with(goal=" "),
        _payload_with(goal=" Goal"),
        _payload_with(goal="First line\nSecond line"),
        _payload_with(goal="First line\vSecond line"),
        _payload_with(goal="First line\x85Second line"),
        _payload_with(goal="First line\u2028Second line"),
        _payload_with(goal="First line\u2029Second line"),
        _payload_with(goal="First\u200bline"),
        _payload_with(goal="lone surrogate: \ud800"),
        _payload_with(goal="x" * 2049),
        _payload_with(scope_decisions=[]),
        _payload_with(scope_decisions="No scope changes."),
        _payload_with(scope_decisions=["Decision.", "Decision."]),
        _payload_with(scope_decisions=["Decision. "]),
        _payload_with(implementation_plan=[" "]),
        _payload_with(implementation_plan=["First line\nSecond line"]),
        _payload_with(implementation_plan=["x" * 2001]),
        _payload_with(acceptance_criteria=["criterion"] * 21),
        _payload_with(scopeDecisions=["None."]),
        _payload_with(unexpected="not allowed"),
    ],
)
def test_parse_rejects_schema_violations_without_coercion(response):
    with pytest.raises(EngineeringBriefParseError):
        parse_engineering_brief(response)


@pytest.mark.asyncio
async def test_compile_brief_returns_brief_and_feeds_spec_and_digest():
    complete, calls = _make_complete(VALID_RESPONSE)

    compilation = await compile_brief(
        title="Bot filter",
        spec="Build a bot filter.",
        repo_digest="### Files\napp/page.tsx",
        verification_context="gated on `npm run build`",
        complete=complete,
    )

    assert compilation.status is EngineeringBriefCompilationStatus.parsed
    assert compilation.markdown == RENDERED_BRIEF
    system, user = calls[0]
    assert system == BRIEF_SYSTEM
    assert '"schema_version": "engineering_brief@1"' in system
    assert "Output JSON only" in system
    assert "Build a bot filter." in user
    assert "app/page.tsx" in user
    assert "npm run build" in user


def test_build_brief_user_adds_bounded_correction_without_rejected_output():
    inputs = {
        "title": "Bot filter",
        "spec": "Build a bot filter.",
        "repo_digest": "### Files\napp/page.tsx",
        "verification_context": "gated on `npm run build`",
    }
    initial_user = build_brief_user(**inputs)
    corrected_user = build_brief_user(
        **inputs,
        correction=True,
    )

    assert corrected_user.startswith(initial_user + "\n\n")
    correction = corrected_user.removeprefix(initial_user + "\n\n")
    assert correction.startswith("# Required format correction")
    assert "previous response violated the engineering_brief@1 schema" in correction
    assert "Output exactly one JSON object" in correction
    assert "previous response:" not in correction.lower()


@pytest.mark.asyncio
async def test_compile_brief_marks_the_correction_prompt():
    complete, calls = _make_complete(VALID_RESPONSE)

    compilation = await compile_brief(
        title="Bot filter",
        spec="Build a bot filter.",
        repo_digest="### Files\napp/page.tsx",
        verification_context="gated on `npm run build`",
        complete=complete,
        correction=True,
    )

    assert compilation.status is EngineeringBriefCompilationStatus.parsed
    assert compilation.markdown == RENDERED_BRIEF
    assert "# Required format correction" in calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected_status"),
    [
        (None, EngineeringBriefCompilationStatus.unavailable),
        (
            "## Goal\nLegacy Markdown is no longer the canonical contract.",
            EngineeringBriefCompilationStatus.invalid,
        ),
        (
            "!touch /tmp/helper-command\n\n" + VALID_RESPONSE,
            EngineeringBriefCompilationStatus.invalid,
        ),
        (
            "/run touch /tmp/helper-command\n\n" + VALID_RESPONSE,
            EngineeringBriefCompilationStatus.invalid,
        ),
        (_payload_with(goal=""), EngineeringBriefCompilationStatus.invalid),
    ],
)
async def test_compile_brief_classifies_unusable_output(reply, expected_status):
    complete, _calls = _make_complete(reply)

    compilation = await compile_brief(
        title="t",
        spec="s",
        repo_digest="d",
        verification_context="v",
        complete=complete,
    )

    assert compilation.status is expected_status
    assert compilation.markdown is None
