"""Strict experiment-design output is rejected, never silently repaired."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.framework import tool_loop
from app.framework.context import AgentContext
from app.graphs import experiment_design
from app.graphs.experiment_design import ExperimentDesignAgent
from app.llm.router import ToolCompletion
from app.llm.prompts.experiment import EXPERIMENT_DESIGN_SYSTEM


def _design() -> dict:
    return {
        "experiment_id": "exp_demo",
        "source_insight": "Checkout drop-off",
        "hypothesis": "A shorter checkout will improve purchase conversion.",
        "description": "Test a shorter checkout.",
        "treatment_spec": "Remove the optional profile step behind the experiment flag.",
        "variants": [
            {"key": "control", "weight": 50, "description": "Current checkout"},
            {"key": "treatment", "weight": 50, "description": "Short checkout"},
        ],
        "primary_metric": {
            "event": "purchase",
            "type": "conversion",
            "direction": "increase",
        },
        "targeting": {"conditions": []},
        "estimated_duration_days": 14,
        "statistical_plan": {
            "protocol": "fixed_horizon_fisher_newcombe_cc_plan_v1",
            "baseline_conversion_rate": 0.1,
            "minimum_detectable_effect": 0.02,
            "significance_level": 0.05,
            "nominal_power": 0.8,
            "required_sample_size_per_arm": 5000,
            "data_settlement_seconds": 300,
        },
        "flag_config": {
            "key": "exp_demo",
            "name": "Demo experiment",
            "default_variant": "control",
            "variants": [
                {"key": "control", "weight": 1},
                {"key": "treatment", "weight": 1},
            ],
            "rules": [],
            "fallthrough": {
                "rollout": {"percentage": 100, "bucket_by": "user_id"}
            },
            "evaluation_mode": "client",
            "auto_disable": False,
        },
    }


def test_parse_preserves_descriptions_and_strict_flag_projection() -> None:
    parsed = ExperimentDesignAgent().parse(json.dumps([_design()]))

    assert parsed[0]["variants"][0]["description"] == "Current checkout"
    assert parsed[0]["flag_config"]["variants"] == [
        {"key": "control", "weight": 1},
        {"key": "treatment", "weight": 1},
    ]
    assert parsed[0]["flag_config"]["fallthrough"]["rollout"]["bucket_by"] == "user_id"


@pytest.mark.parametrize("duration", [1, 90])
def test_duration_contract_accepts_inclusive_boundaries(duration: int) -> None:
    design = _design()
    design["estimated_duration_days"] = duration

    parsed = ExperimentDesignAgent().parse(json.dumps([design]))

    assert parsed[0]["estimated_duration_days"] == duration


@pytest.mark.parametrize("duration", [0, 91, 14.5, 14.0, True, "14"])
def test_duration_contract_rejects_out_of_range_or_non_integer_values(
    duration: object,
) -> None:
    design = _design()
    design["estimated_duration_days"] = duration

    with pytest.raises(ValueError, match="estimated_duration_days"):
        ExperimentDesignAgent().parse(json.dumps([design]))


def test_duration_validation_preserves_rejected_scalar_for_correction() -> None:
    design = _design()
    design["estimated_duration_days"] = 607

    with pytest.raises(ValueError, match=r"received 607"):
        ExperimentDesignAgent().parse(json.dumps([design]))


def test_prompt_exposes_duration_feasibility_and_statistical_policy() -> None:
    assert "integer from 1 through 90 inclusive" in EXPERIMENT_DESIGN_SYSTEM
    assert "required sample cannot enroll within 90" in EXPERIMENT_DESIGN_SYSTEM
    assert "Never clamp a longer estimate to 90 days" in EXPERIMENT_DESIGN_SYSTEM
    assert "significance_level 0.05" in EXPERIMENT_DESIGN_SYSTEM
    assert "nominal_power 0.80" in EXPERIMENT_DESIGN_SYSTEM
    assert "treatment_count exactly len(variants) - 1" in EXPERIMENT_DESIGN_SYSTEM
    assert "inflate minimum_detectable_effect" in EXPERIMENT_DESIGN_SYSTEM


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("significance_level", 0.000001),
        ("nominal_power", 0.51),
    ],
)
def test_builtin_design_rejects_noncanonical_statistical_settings(
    field: str,
    value: float,
) -> None:
    design = _design()
    design["statistical_plan"][field] = value

    with pytest.raises(ValueError, match=field):
        ExperimentDesignAgent().parse(json.dumps([design]))


class _NoMemory:
    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    async def log(
        self,
        run_id: str,
        action_type: str,
        config: dict[str, Any],
        **kwargs: Any,
    ) -> int:
        self.entries.append((run_id, action_type, config))
        return len(self.entries)


@pytest.mark.parametrize("replacement_kind", ["omit", "clamp", "rename"])
@pytest.mark.asyncio
async def test_agent_retries_final_duration_error_with_full_contract(
    monkeypatch,
    replacement_kind: str,
) -> None:
    rejected = _design()
    rejected["estimated_duration_days"] = 607
    rejected_text = json.dumps([rejected])
    clamped = copy.deepcopy(rejected)
    clamped["estimated_duration_days"] = 90
    if replacement_kind == "rename":
        clamped["experiment_id"] = "exp_renamed"
        clamped["flag_config"]["key"] = "exp_renamed"
        clamped["source_insight"] = "Renamed infeasible insight"
    replacement_text = (
        "[]" if replacement_kind == "omit" else json.dumps([clamped])
    )
    calls = {"n": 0}

    async def fake_active_experiments(**kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def fake_chat(model_tier, messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            assert "Analytics observation window: 7 days" in messages[1]["content"]
            return ToolCompletion(text=rejected_text)

        assert kwargs["force_text"] is True
        assert kwargs["context"].purpose == (
            "agent.experiment_design.output_correction"
        )
        assert messages[-2] == {"role": "assistant", "content": rejected_text}
        assert "estimated_duration_days" in messages[-1]["content"]
        assert "received 607" in messages[-1]["content"]
        assert "omit/empty-output" in messages[-1]["content"]
        return ToolCompletion(text=replacement_text)

    monkeypatch.setattr(
        experiment_design,
        "get_active_experiments",
        fake_active_experiments,
    )
    monkeypatch.setattr(tool_loop, "chat_completion_with_tools", fake_chat)
    audit = _RecordingAudit()
    ctx = AgentContext(
        pool=None,
        llm_runtime=object(),
        vector_store=_NoMemory(),
        project_id="demo",
        run_id="run-1",
        lease_owner_id="worker-1",
        time_range_days=7,
        audit=audit,
    )

    state = {
        "insights": [
            {
                "title": "Blog visitors do not reach upload",
                "action_type": "experiment",
                "recommended_action": "Experiment with an upload CTA.",
            }
        ]
    }
    if replacement_kind != "omit":
        with pytest.raises(ValueError, match="must be omitted, not clamped"):
            await ExperimentDesignAgent().run(ctx, state)
    else:
        result = await ExperimentDesignAgent().run(ctx, state)
        assert result.output == []
        assert result.metadata["needs_approval"] is False

    assert calls["n"] == 2
    validation_events = [
        entry
        for entry in audit.entries
        if entry[1] == "experiment_design_output_validation_failed"
    ]
    assert len(validation_events) == (2 if replacement_kind != "omit" else 1)


def test_experiment_bucket_identity_is_exact_and_prompt_prefers_browser_identity() -> None:
    anonymous = _design()
    anonymous["flag_config"]["fallthrough"]["rollout"]["bucket_by"] = "anonymous_id"
    assert ExperimentDesignAgent().parse(json.dumps([anonymous]))[0]["flag_config"][
        "fallthrough"
    ]["rollout"]["bucket_by"] == "anonymous_id"

    for invalid in ("account_id", "", None):
        design = _design()
        design["flag_config"]["fallthrough"]["rollout"]["bucket_by"] = invalid
        with pytest.raises(ValueError, match="invalid experiment design"):
            ExperimentDesignAgent().parse(json.dumps([design]))

    missing = _design()
    del missing["flag_config"]["fallthrough"]["rollout"]["bucket_by"]
    with pytest.raises(ValueError, match="invalid experiment design"):
        ExperimentDesignAgent().parse(json.dumps([missing]))

    assert '"bucket_by": "anonymous_id"' in EXPERIMENT_DESIGN_SYSTEM
    assert 'Prefer "anonymous_id" for browser experiments' in EXPERIMENT_DESIGN_SYSTEM


@pytest.mark.parametrize(
    "mutate",
    [
        lambda design: design.update({"secondary_metrics": []}),
        lambda design: design["flag_config"]["variants"][0].update(
            {"description": "must not be repaired away"}
        ),
        lambda design: design["flag_config"]["rules"].append(
            {
                "rollout": {"percentage": 10, "bucket_by": "user_id"},
            }
        ),
    ],
)
def test_parse_rejects_unknown_or_noncanonical_fields(mutate) -> None:
    design = copy.deepcopy(_design())
    mutate(design)

    with pytest.raises(ValueError, match="invalid experiment design"):
        ExperimentDesignAgent().parse(json.dumps([design]))


def test_parse_rejects_single_object_and_duplicate_ids() -> None:
    agent = ExperimentDesignAgent()
    with pytest.raises(ValueError, match="JSON array"):
        agent.parse(json.dumps(_design()))

    with pytest.raises(ValueError, match="unique experiment_id"):
        agent.parse(json.dumps([_design(), _design()]))


@pytest.mark.parametrize("field", ["experiment_id", "flag_config.key"])
def test_parse_rejects_ids_config_would_refuse(field: str) -> None:
    design = _design()
    if field == "experiment_id":
        design["experiment_id"] = "experiment with spaces"
    else:
        design["flag_config"]["key"] = "flag/with/path"

    with pytest.raises(ValueError, match="invalid experiment design"):
        ExperimentDesignAgent().parse(json.dumps([design]))
