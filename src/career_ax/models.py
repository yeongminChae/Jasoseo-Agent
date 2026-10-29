"""Explicit contracts shared by all agent runners and the deterministic verifier."""
from __future__ import annotations
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class SourceSpec(StrictModel):
    source_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    provenance: Literal["primary", "secondary", "synthetic"]
    phase: Literal["t0_input", "t0_output", "posthoc"] = "t0_input"
    note: str = ""

class Manifest(StrictModel):
    sources: list[SourceSpec] = Field(min_length=1)

class EvidenceRef(StrictModel):
    source_id: str = Field(min_length=1)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=300)

class CandidateLine(StrictModel):
    source_id: str
    line: int
    text: str
    provenance: Literal["primary", "secondary", "synthetic"]

class Plan(StrictModel):
    question: str = Field(min_length=5)
    rationale: str = Field(min_length=1)

class Collection(StrictModel):
    evidence: list[EvidenceRef]
    missing: list[str] = Field(default_factory=list)

class Finding(StrictModel):
    finding_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    kind: Literal["observation", "hypothesis", "uncertain"]
    statement: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    limits: str = ""

class Assessment(StrictModel):
    findings: list[Finding] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

class NextAction(str, Enum):
    CONTINUE = "CONTINUE"
    COMPLETE = "COMPLETE"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCKED = "BLOCKED"

class Decision(StrictModel):
    action: NextAction
    reason: str = Field(min_length=1)
    next_question: str | None = None
    based_on_finding_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == NextAction.CONTINUE and (not self.next_question or not self.based_on_finding_ids):
            raise ValueError("CONTINUE requires next_question and finding IDs")
        if self.action != NextAction.CONTINUE and self.next_question is not None:
            raise ValueError("terminal action cannot have next_question")
        return self

class Verification(StrictModel):
    passed: bool
    errors: list[str]
    valid_finding_ids: list[str]
    source_level: Literal["PRIMARY_OR_SYNTHETIC", "SECONDARY_ONLY", "NO_EVIDENCE"]
    # Structural grounding, not semantic proof.

class CycleRecord(StrictModel):
    index: int
    question: str
    based_on_finding_ids: list[str] = Field(default_factory=list)
    collection: Collection
    analyst: Assessment
    critic: Assessment
    verification: Verification
    decision: Decision
    next_question: str | None = None
    accepted_finding_ids: list[str] = Field(default_factory=list)

class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL_COMPLETE = "PARTIAL_COMPLETE"
    BLOCKED = "BLOCKED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"

class AgentCall(StrictModel):
    role: Literal["lead_plan", "collector", "analyst", "critic", "lead_next"]
    cycle: int
    input_sha256: str
    output_sha256: str
    mode: Literal["mock", "codex"]
    # Does NOT pretend model token usage or dollar cost is available.

class FinalReport(StrictModel):
    run_id: str
    goal: str
    status: RunStatus
    runner: Literal["mock", "codex"]
    is_real_llm: bool
    source_snapshot: str
    cycles: list[CycleRecord]
    calls_used: int = 0
    model_cost_usd: float | None = None
    notes: list[str] = Field(default_factory=list)
