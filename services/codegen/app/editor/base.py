"""The editing-engine seam.

``Editor`` is the interface codegen uses to turn a task spec into a gated local
candidate. Production uses an Aider-backed implementation (model-agnostic via
LiteLLM); tests use a fake. The controller, not the editor, reconstructs and
pushes the returned patch. Keeping the engine behind a Protocol makes the engine
— and the model — a config choice, not a rewrite (plan decision D3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from app.contracts.models import ContractBundle
from app.inspection.models import DependencySlice, InspectionSnapshot
from app.llm.contracts import LlmExecutionAuthority
from app.requirements.models import RequirementLedger
from app.runtime.models import (
    GeneratedRuntimeWorkflowAttestation,
    RuntimeAcceptancePlan,
    RuntimeAcceptancePolicy,
)
from app.safety.policy import (
    EffectiveCodegenSafetyPolicy,
    PlatformCodegenSafetyPolicy,
    TenantCodegenConnectionPolicy,
    resolve_effective_policy,
)
from app.semantic_review.models import ReviewVerdict
from app.verification.models import VerificationCoverage, VerificationPlan

if TYPE_CHECKING:
    from app.inspection.preparation import RepositoryPreparationEvidence


def _default_effective_safety_policy() -> EffectiveCodegenSafetyPolicy:
    """Safe constructor for direct/custom editor callers and tests."""
    return resolve_effective_policy(
        TenantCodegenConnectionPolicy(), PlatformCodegenSafetyPolicy()
    )


_SAFE_EDIT_FAILURE_MESSAGES: dict[tuple[str, str], str] = {
    (
        "brief",
        "invalid_model_output",
    ): "Engineering brief model output did not match the required schema.",
    (
        "brief",
        "model_unavailable",
    ): "Engineering brief model was unavailable.",
    (
        "brief",
        "structured_output_unavailable",
    ): "Engineering brief structured-output enforcement was unavailable.",
}


@dataclass(frozen=True)
class SafeEditFailure:
    """Allowlisted failure metadata safe to cross the sandbox boundary.

    The worker must never put provider output, prompts, exception text, or other
    free-form data in this structure.  Keeping the accepted pairs closed lets
    the controller map them to fixed messages without trusting worker text.
    """

    stage: Literal["brief"]
    code: Literal[
        "invalid_model_output",
        "model_unavailable",
        "structured_output_unavailable",
    ]

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not str
            or type(self.code) is not str
            or (self.stage, self.code) not in _SAFE_EDIT_FAILURE_MESSAGES
        ):
            raise ValueError("edit failure diagnostic is not allowlisted")

    @classmethod
    def from_worker_payload(cls, value: object) -> Self | None:
        """Accept only the exact canonical worker object, without coercion."""
        if type(value) is not dict or set(value) != {"stage", "code"}:
            return None
        stage = value.get("stage")
        code = value.get("code")
        if type(stage) is not str or type(code) is not str:
            return None
        if (stage, code) not in _SAFE_EDIT_FAILURE_MESSAGES:
            return None
        return cls(stage="brief", code=code)

    def to_worker_payload(self) -> dict[str, str]:
        """Return the only JSON shape accepted by the controller."""
        return {"stage": self.stage, "code": self.code}

    @property
    def safe_error(self) -> str:
        """Return fixed operator-visible text containing no worker data."""
        return _SAFE_EDIT_FAILURE_MESSAGES[(self.stage, self.code)]


@dataclass
class EditRequest:
    """Everything the engine needs to implement one local candidate."""

    repo: str  # owner/name
    base_branch: str
    branch: str  # branch identity the controller will eventually publish
    token: str  # short-lived read-only installation token, scoped to this repo
    title: str
    spec: str
    #: Durable controller identity used for project LLM attempt binding.
    #: Tenant production callers always provide it; direct trusted-local callers
    #: may omit it.
    changeset_id: str = ""
    #: Tenant boundary for private dependency-contract caches. Legacy/custom
    #: callers may omit it; the editor then scopes evidence to ``repo``.
    project_scope: str = ""
    #: Stable requirement contract reused for same-PR CI repairs. Initial runs
    #: omit it and compile one deterministically from the task source.
    requirement_ledger: RequirementLedger | None = None
    inspection_snapshot: InspectionSnapshot | None = None
    dependency_slice: DependencySlice | None = None
    verification_plan: VerificationPlan | None = None
    verification_coverage: VerificationCoverage | None = None
    runtime_acceptance_plan: RuntimeAcceptancePlan | None = None
    #: Exact source-tree-bound profile, plans, and dependency evidence produced
    #: by the provider-free preparation container. The isolated editor requires
    #: this canonical bundle before repository text can reach a model.
    repository_preparation: RepositoryPreparationEvidence | None = None
    runtime_acceptance_policy: RuntimeAcceptancePolicy = field(
        default_factory=RuntimeAcceptancePolicy
    )
    constraints: list[str] = field(default_factory=list)
    #: Repo verification command exposed as guidance so the generated change
    #: includes compatible tests. GitHub CI, not APDL, executes it authoritatively.
    test_cmd: str | None = None
    #: Trusted, authority-resolved safety policy. Tenant JSON never crosses the
    #: editor boundary. The engine evaluates this policy on the FULL diff before
    #: returning a publishable patch, so a violating branch never reaches the
    #: controller's write boundary.
    safety_policy: EffectiveCodegenSafetyPolicy = field(
        default_factory=_default_effective_safety_policy
    )
    #: Merge-commit SHA to revert deterministically (``git revert``) instead of
    #: asking the agent to reconstruct the revert from prose. The agent is still
    #: invoked afterwards if verification fails on the reverted tree.
    revert_sha: str | None = None
    #: Update the already-pushed PR branch instead of cutting a new branch.
    existing_branch: bool = False
    #: Exact failed PR head a repair is allowed to extend. A mismatch blocks
    #: editing and the controller push uses a force-with-lease for this SHA.
    expected_head_sha: str | None = None
    #: Risk controls whether unavailable/unparseable auxiliary model gates may
    #: fail open. Only low-risk changes may skip them.
    risk_level: str = "low"
    #: Ephemeral per-call broker capability supplied by the controller. It
    #: contains no provider credential; production editors exchange it for one
    #: phase-scoped lease immediately before each provider invocation.
    llm_execution: LlmExecutionAuthority | None = field(
        default=None,
        repr=False,
    )


@dataclass
class EditResult:
    """Outcome of one edit attempt."""

    success: bool
    branch: str | None = None
    diff_stat: dict[str, Any] = field(default_factory=dict)
    changed_paths: list[str] = field(default_factory=list)
    diff_text: str = ""
    error: str | None = None
    #: Optional closed diagnostic emitted by a failed isolated worker. The
    #: controller persists only its fixed ``safe_error`` mapping, so adding
    #: actionable diagnostics does not require a database schema change.
    safe_failure: SafeEditFailure | None = None
    logs_uri: str | None = None
    #: Local candidate commit identity. This is not a remote branch identity;
    #: initial generation and repair replace it with the controller-published
    #: SHA before durable GitHub projection.
    head_sha: str | None = None
    #: Exact remote commit the binary patch applies to.
    base_sha: str | None = None
    #: Git tree identity the controller must reproduce before it may push.
    candidate_tree_sha: str | None = None
    #: Canonical base64 encoding of ``git diff --binary --full-index``.
    patch_base64: str | None = None
    #: Exact installed dependency evidence used by this attempt. This is model
    #: grounding, never an APDL-local CI result.
    contract_bundle: ContractBundle | None = None
    requirement_ledger: RequirementLedger | None = None
    inspection_snapshot: InspectionSnapshot | None = None
    dependency_slice: DependencySlice | None = None
    verification_plan: VerificationPlan | None = None
    verification_coverage: VerificationCoverage | None = None
    runtime_acceptance_plan: RuntimeAcceptancePlan | None = None
    generated_runtime_workflow: GeneratedRuntimeWorkflowAttestation | None = None
    review_verdict: ReviewVerdict | None = None
    #: Ordered transcript of the LLM prompts this attempt actually sent — the
    #: brief compilation, each edit instruction handed to the coding agent, and
    #: each pre-push diff review. Entries are
    #: ``{"stage", "label", "system", "user", "notes"}`` dicts; ``system`` is
    #: ``None`` for the edit stage (Aider supplies its own system prompt).
    #: Populated on failure too, when maintainers most need to see what the model
    #: was told.
    prompts: list[dict[str, Any]] = field(default_factory=list)


class Editor(Protocol):
    """Implements, reviews, and gates a local candidate patch.

    Editor implementations receive only repository read authority. They must
    enforce the canonical gates and return an exact base/tree-bound binary
    patch. Model output remains untrusted until the controller reconstructs and
    verifies that tree through :mod:`app.github.publisher`.

    Implementations MUST NOT raise for an ordinary failed attempt (editing or
    safety budget exhausted) — return ``EditResult(success=False, error=...)``
    so the job can record a clean ``tests_failed``. Reserve exceptions for
    genuinely unexpected faults.
    """

    async def implement(self, request: EditRequest) -> EditResult: ...
