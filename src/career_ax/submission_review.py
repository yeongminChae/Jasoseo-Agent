"""Submission Review skeleton. Stub execution is plumbing validation, not AI review."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ConfigDict, Field, model_validator

from .models import Assessment, EvidenceRef, Finding, StrictModel, Verification
from .runner import AgentRunner


class T0Source(StrictModel):
    """Caller-curated original material; no paths, previous results, or gold labels."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1)
    purpose: Literal["question", "submission", "rubric", "reference"]
    phase: Literal["t0_input"]
    provenance: Literal["primary", "secondary", "synthetic"]
    text: str = Field(min_length=1, max_length=600_000)


class ReviewRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1)
    sources: tuple[T0Source, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self):
        if len({s.source_id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source ID")
        if not {"question", "submission"}.issubset({s.purpose for s in self.sources}):
            raise ValueError("Original question and submission sources are required")
        if any(not s.text.strip() for s in self.sources):
            raise ValueError("Blank source text")
        return self

    @property
    def input_sha256(self) -> str:
        """Shared baseline/AX input identity; excludes run-specific request_id."""
        data = [s.model_dump(mode="json") for s in self.sources]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ReviewResult(StrictModel):
    request_id: str
    input_sha256: str
    execution_mode: Literal["stub", "custom"]
    real_llm_executed: bool | None  # None = unknown for injected developer code.
    status: Literal["STRUCTURAL_CHECKS_ONLY"] = "STRUCTURAL_CHECKS_ONLY"
    semantic_review_complete: Literal[False] = False
    integration_policy: Literal["TODO"] = "TODO"
    analyst: Assessment
    critic: Assessment
    verification: Verification


class ReviewInput(TypedDict):
    request: ReviewRequest


class AnalystUpdate(TypedDict):
    analyst: Assessment


class CriticUpdate(TypedDict):
    critic: Assessment


class ReviewOutput(TypedDict):
    result: ReviewResult


class ReviewState(ReviewInput, total=False):
    analyst: Assessment
    critic: Assessment
    result: ReviewResult


Reviewer = Callable[[ReviewRequest, AgentRunner], Assessment]


def run_analyst(request: ReviewRequest, runner: AgentRunner) -> Assessment:
    """TODO(developer): analyst prompt and runner.run(..., Assessment) implementation.

    Use only request's original sources, treat them as untrusted data, and use
    role-prefixed finding IDs. Never read graph state, prior conclusions or gold.
    """
    raise NotImplementedError("Submission Review Analyst prompt/execution is not implemented")


def run_critic(request: ReviewRequest, runner: AgentRunner) -> Assessment:
    """TODO(developer): independent critic prompt and runner invocation, same T0 input.

    No Analyst result, shared conversation memory, or previous-run retrieval.
    """
    raise NotImplementedError("Submission Review Critic prompt/execution is not implemented")


def reconcile_assessments(
    request: ReviewRequest, analyst: Assessment, critic: Assessment, verification: Verification,
) -> Assessment:
    """TODO(developer): integrate results and resolve disagreement after verification.

    Not wired into MVP; raw assessments stay separate and no finding is promoted.
    """
    raise NotImplementedError("Integration/conflict policy is not implemented")


def decide_failure_action(
    error: Exception, *, role: Literal["analyst", "critic", "verifier"], attempt: int,
) -> Literal["retry", "abort", "human_review"]:
    """TODO(developer): bounded retry/abort policy; MVP simply propagates errors.

    This extension point is not wired into the graph and makes no retry claim.
    """
    raise NotImplementedError("Retry/abort decision policy is not implemented")


def _isolated_request(state: ReviewInput) -> ReviewRequest:
    raw = state["request"]
    # Revalidate and allocate a fresh, recursively frozen object per branch.
    return ReviewRequest.model_validate(raw.model_dump() if isinstance(raw, ReviewRequest) else raw)


def analyst_node(state: ReviewInput, *, runner: AgentRunner, review: Reviewer = run_analyst) -> AnalystUpdate:
    result = review(_isolated_request(state), runner)
    return {"analyst": Assessment.model_validate(result.model_dump())}


def critic_node(state: ReviewInput, *, runner: AgentRunner, review: Reviewer = run_critic) -> CriticUpdate:
    result = review(_isolated_request(state), runner)
    return {"critic": Assessment.model_validate(result.model_dump())}


def verify_sources(request: ReviewRequest, analyst: Assessment, critic: Assessment) -> Verification:
    """Check citation location and ID structure only; no semantic or merge policy."""
    sources = {s.source_id: s for s in request.sources}
    seen: set[str] = set()
    valid_ids: list[str] = []
    errors: list[str] = []
    levels: set[str] = set()
    for role, assessment in (("analyst", analyst), ("critic", critic)):
        for finding in assessment.findings:
            local_errors = []
            if finding.finding_id in seen:
                local_errors.append(f"Duplicate finding ID: {finding.finding_id}")
            seen.add(finding.finding_id)
            if not finding.evidence:
                local_errors.append(f"{role}:{finding.finding_id} missing citation")
            for ref in finding.evidence:
                source = sources.get(ref.source_id)
                lines = source.text.splitlines() if source else []
                if not (1 <= ref.line <= len(lines) and ref.quote in lines[ref.line - 1]):
                    local_errors.append(f"{role}:{finding.finding_id} invalid citation")
                elif source:
                    levels.add(source.provenance)
            errors.extend(local_errors)
            if not local_errors:
                valid_ids.append(finding.finding_id)
    level = ("PRIMARY_OR_SYNTHETIC" if levels & {"primary", "synthetic"}
             else "SECONDARY_ONLY" if levels else "NO_EVIDENCE")
    return Verification(passed=not errors, errors=errors, valid_finding_ids=valid_ids, source_level=level)


def verifier_node(state: ReviewState, *, execution_mode: Literal["stub", "custom"]) -> ReviewOutput:
    request = _isolated_request(state)
    analyst, critic = state["analyst"], state["critic"]
    return {"result": ReviewResult(
        request_id=request.request_id, input_sha256=request.input_sha256,
        execution_mode=execution_mode, real_llm_executed=False if execution_mode == "stub" else None,
        analyst=analyst, critic=critic, verification=verify_sources(request, analyst, critic),
    )}


def build_review_graph(
    runner: AgentRunner, *, analyst: Reviewer = run_analyst, critic: Reviewer = run_critic,
):
    """Compile independent fan-out + barrier. No checkpointer or research store."""
    mode = ("stub" if type(runner) is SubmissionStubRunner
            and analyst is stub_analyst and critic is stub_critic else "custom")
    graph = StateGraph(ReviewState, input_schema=ReviewInput, output_schema=ReviewOutput)

    def analyze(state: ReviewInput):
        return analyst_node(state, runner=runner, review=analyst)

    def critique(state: ReviewInput):
        return critic_node(state, runner=runner, review=critic)

    def verify(state: ReviewState):
        return verifier_node(state, execution_mode=mode)

    graph.add_node("analyst", analyze, input_schema=ReviewInput)
    graph.add_node("critic", critique, input_schema=ReviewInput)
    graph.add_node("verifier", verify)
    graph.add_edge(START, "analyst")
    graph.add_edge(START, "critic")
    graph.add_edge(["analyst", "critic"], "verifier")
    graph.add_edge("verifier", END)
    return graph.compile()


class SubmissionStubRunner(AgentRunner):
    """Synthetic plumbing stub. No LLM calls or generic review heuristics."""

    mode = "stub"

    def run(self, role, payload, response_type):
        if role not in {"analyst", "critic"} or response_type is not Assessment:
            raise ValueError("Submission Stub supports analyst/critic Assessment only")
        request = ReviewRequest.model_validate(payload)
        if any(s.provenance != "synthetic" for s in request.sources):
            raise ValueError("Submission Stub accepts synthetic sources only")
        source = next(s for s in request.sources if s.purpose == "submission")
        line, text = next((i, text) for i, text in enumerate(source.text.splitlines(), 1) if text.strip())
        return Assessment(findings=[Finding(
            finding_id=f"stub_{role}_1", category="stub_wiring", kind="uncertain",
            statement="합성 Stub: 입력과 인용 연결만 확인; 실제 내용 검토 미구현",
            evidence=[EvidenceRef(source_id=source.source_id, line=line, quote=text[:300])],
            limits="LLM 성능 또는 자기소개서 품질 평가가 아님",
        )], unresolved=["실제 역할별 검토 및 통합 정책 TODO"])


def stub_analyst(request: ReviewRequest, runner: AgentRunner) -> Assessment:
    return runner.run("analyst", request.model_dump(mode="json"), Assessment)


def stub_critic(request: ReviewRequest, runner: AgentRunner) -> Assessment:
    return runner.run("critic", request.model_dump(mode="json"), Assessment)


def markdown_review(result: ReviewResult) -> str:
    lines = ["# Submission Review MVP", "", f"- Request: {result.request_id}",
             f"- Input SHA256: {result.input_sha256}", f"- Mode: {result.execution_mode}",
             "- Status: STRUCTURAL_CHECKS_ONLY (semantic review incomplete)",
             "- Integration/conflict policy: TODO",
             f"- Structural checks passed: {result.verification.passed}",
             "", "합성 Stub은 실행 골격 검증용이며 실제 LLM 성능을 나타내지 않습니다."]
    for role, assessment in (("Analyst", result.analyst), ("Critic", result.critic)):
        lines.extend(["", f"## {role}"])
        for finding in assessment.findings:
            lines.append(f"- [{finding.kind}] {finding.statement}")
            lines.extend(f"  - {e.source_id}:{e.line}: {e.quote}" for e in finding.evidence)
        lines.extend(f"- Unresolved: {item}" for item in assessment.unresolved)
    lines.extend(["", "## Structural errors", *[f"- {e}" for e in result.verification.errors]])
    return "\n".join(lines) + "\n"
