"""Pure in-memory and persisted models owned by the agent loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibesys.schemas import CandidateDisposition, HypothesisOutcome, OrchestratorPlan

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class HypothesisResolution(StrEnum):
    """Framework-owned resolution after all available evidence is known."""

    PROVEN = "proven"
    DISPROVEN = "disproven"
    INCONCLUSIVE = "inconclusive"
    IMPLEMENTATION_FAILED = "implementation_failed"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class HypothesisReview(StrEnum):
    """Independent review state, separate from empirical resolution."""

    PENDING = "pending"
    PASS = "pass"  # noqa: S105  # tracked: #288
    FAIL = "fail"
    DEFERRED = "deferred"


class HypothesisStrategyState(StrEnum):
    """Orchestrator-owned strategic treatment of a research direction."""

    ACTIVE = "active"
    COMPLETED = "completed"
    PARKED = "parked"
    ABANDONED = "abandoned"


class HypothesisMeasurement(BaseModel):
    """Official headline measurement and its causal comparison baseline."""

    model_config = ConfigDict(extra="forbid")

    round: Annotated[int, Field(gt=0)]
    metric: str = Field(min_length=1)
    value: FiniteFloat
    unit: str | None = None
    direction: Literal["max", "min"]
    baseline_round: Annotated[int, Field(gt=0)] | None = None
    baseline_commit: str | None = None
    baseline_value: FiniteFloat | None = None
    delta_pct: FiniteFloat | None = None


class HypothesisRecord(BaseModel):
    """Authoritative lifecycle state for one experimental hypothesis."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    hypothesis_id: str = Field(min_length=1)
    claim: str | None = None
    task: str | None = None
    started_round: Annotated[int, Field(gt=0)]
    rounds: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list)
    parent_round: Annotated[int, Field(gt=0)] | None = None
    parent_commit: str | None = None
    declared_outcome: HypothesisOutcome | None = None
    review: HypothesisReview = HypothesisReview.PENDING
    resolution: HypothesisResolution | None = None
    measurement: HypothesisMeasurement | None = None
    candidate_retained: bool | None = None
    strategy: HypothesisStrategyState = HypothesisStrategyState.ACTIVE
    strategy_reason: str | None = None


class HypothesisLedger(BaseModel):
    """Versioned portable source of truth for every run hypothesis."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = 1
    hypotheses: list[HypothesisRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_identity(self) -> HypothesisLedger:
        identifiers = [item.hypothesis_id for item in self.hypotheses]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("hypothesis IDs must be unique")  # noqa: TRY003  # tracked: #288
        active = [
            item for item in self.hypotheses if item.strategy is HypothesisStrategyState.ACTIVE
        ]
        if len(active) > 1:
            raise ValueError("at most one hypothesis may be active")  # noqa: TRY003  # tracked: #288
        return self

    def by_id(self, hypothesis_id: str) -> HypothesisRecord | None:
        """Return one hypothesis by stable ID."""
        return next(
            (item for item in self.hypotheses if item.hypothesis_id == hypothesis_id),
            None,
        )


class ActiveHypothesis(BaseModel):
    """Versioned continuation state for one implementer goal."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = 1
    plan: OrchestratorPlan
    started_round: Annotated[int, Field(gt=0)]
    parent_round: Annotated[int, Field(gt=0)] | None = None
    parent_commit: str | None = None
    feedback: str | None = None
    next_step: str | None = None
    continuation_rounds: Annotated[int, Field(ge=0)] = 0
    revert_applied: bool = False
    revert_commit: str | None = None
    gate_revalidation_pending: bool = False
    gate_approved_perf_metric: FiniteFloat | None = None
    gate_approved_perf_unit: str | None = None
    gate_approved_metrics: dict[str, FiniteFloat] = Field(default_factory=dict)
    gate_approved_evaluation_artifact: str | None = None
    gate_approved_candidate_disposition: str = CandidateDisposition.UNASSESSED.value
    gate_approved_candidate_metrics: dict[str, FiniteFloat] = Field(default_factory=dict)
    gate_approved_candidate_evaluation_artifact: str | None = None
    gate_approved_candidate_operating_point: str = ""
    gate_approved_candidate_retention_reason: str = ""
    gate_candidate_commit: str | None = None
    gate_accuracy_passed: bool = False

    def clone(self) -> ActiveHypothesis:
        """Return an independent copy for computing the next-round state."""
        return self.model_copy(deep=True)
