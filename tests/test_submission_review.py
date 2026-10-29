"""Graph contracts and synthetic plumbing only; no real-model quality tests."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from career_ax.cli import main
from career_ax.models import Assessment, EvidenceRef, Finding
from career_ax.runner import AgentRunner, AgentRuntimeError
from career_ax.submission_review import (
    ReviewRequest, ReviewResult, SubmissionStubRunner, build_review_graph,
    decide_failure_action, reconcile_assessments,
    stub_analyst, stub_critic, verify_sources,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/submission_review/request.json"


@pytest.fixture
def request_t0():
    return ReviewRequest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def stub_graph():
    return build_review_graph(SubmissionStubRunner(), analyst=stub_analyst, critic=stub_critic)


def test_stub_graph_complete_but_not_semantic_review(request_t0):
    output = stub_graph().invoke({"request": request_t0})
    assert set(output) == {"result"}
    result = ReviewResult.model_validate_json(output["result"].model_dump_json())
    assert result.verification.passed
    assert result.execution_mode == "stub" and result.real_llm_executed is False
    assert result.status == "STRUCTURAL_CHECKS_ONLY"
    assert not result.semantic_review_complete and result.integration_policy == "TODO"
    assert result.analyst.findings[0].finding_id != result.critic.findings[0].finding_id
    assert result.input_sha256 == request_t0.input_sha256


def test_parallel_branches_use_identical_fresh_payloads_and_join_once(request_t0):
    barrier = Barrier(2, timeout=5)
    lock = Lock()

    class FakeRunner(AgentRunner):
        mode = "fake"

        def __init__(self):
            self.calls = []

        def run(self, role, payload, response_type):
            with lock:
                self.calls.append((role, deepcopy(payload)))
            # A sequential graph would break this barrier.
            barrier.wait()
            payload["sources"][0]["text"] = "MUTATION_ONLY_IN_LOCAL_PAYLOAD"
            return response_type(findings=[], unresolved=[role])

    fake = FakeRunner()
    graph = build_review_graph(fake, analyst=stub_analyst, critic=stub_critic)
    # Caller cannot preseed results in the graph's input channels.
    updates = list(graph.stream({"request": request_t0, "analyst": Assessment(unresolved=["POISON"])}))
    assert len([u for u in updates if "verifier" in u]) == 1
    assert len(fake.calls) == 2
    assert {role for role, _ in fake.calls} == {"analyst", "critic"}
    assert fake.calls[0][1] == fake.calls[1][1] == request_t0.model_dump(mode="json")
    assert all(set(payload) == {"request_id", "sources"} for _, payload in fake.calls)
    result = next(u["verifier"]["result"] for u in updates if "verifier" in u)
    assert result.analyst.unresolved == ["analyst"]
    assert result.critic.unresolved == ["critic"]
    assert result.execution_mode == "custom" and result.real_llm_executed is None


@pytest.mark.parametrize("field", ["gold_labels", "golden_dataset", "previous_ai_analysis", "posthoc_correction"])
def test_request_rejects_non_t0_extra_channels(request_t0, field):
    raw = request_t0.model_dump(mode="json")
    raw[field] = "NEVER_SEND_TO_WORKERS"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ReviewRequest.model_validate(raw)


@pytest.mark.parametrize("field,value", [
    ("phase", "posthoc"), ("phase", "t0_output"),
    ("purpose", "golden_dataset"), ("purpose", "previous_ai_analysis"),
])
def test_request_rejects_excluded_source_types(request_t0, field, value):
    raw = request_t0.model_dump(mode="json")
    raw["sources"][0][field] = value
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(raw)


def test_request_validation_and_recursive_freeze(request_t0):
    with pytest.raises(ValidationError, match="frozen"):
        request_t0.sources[0].text = "changed"
    raw = request_t0.model_dump(mode="json")
    raw["sources"].append(raw["sources"][0])
    with pytest.raises(ValidationError, match="Duplicate"):
        ReviewRequest.model_validate(raw)
    raw["sources"] = raw["sources"][:1]
    with pytest.raises(ValidationError, match="question and submission"):
        ReviewRequest.model_validate(raw)


def test_input_digest_can_pair_future_baseline_and_ax(request_t0):
    raw = request_t0.model_dump()
    raw["request_id"] = "baseline-run"
    assert ReviewRequest.model_validate(raw).input_sha256 == request_t0.input_sha256
    raw["sources"] = [s.model_dump() for s in request_t0.sources]
    raw["sources"][1]["text"] += " changed original"
    assert ReviewRequest.model_validate(raw).input_sha256 != request_t0.input_sha256


@pytest.mark.parametrize("role", ["analyst", "critic"])
def test_unimplemented_role_raises_even_with_injected_runner(request_t0, role):
    kwargs = {"analyst": stub_analyst, "critic": stub_critic}
    kwargs.pop(role)
    with pytest.raises(NotImplementedError, match=role.capitalize()):
        build_review_graph(SubmissionStubRunner(), **kwargs).invoke({"request": request_t0})


def test_remaining_policy_todos_are_explicit(request_t0):
    verification = verify_sources(request_t0, Assessment(), Assessment())
    with pytest.raises(NotImplementedError, match="Integration"):
        reconcile_assessments(request_t0, Assessment(), Assessment(), verification)
    with pytest.raises(NotImplementedError, match="Retry"):
        decide_failure_action(TimeoutError(), role="analyst", attempt=1)


def test_runner_failure_propagates_without_hidden_retry(request_t0):
    class FailingRunner(AgentRunner):
        mode = "fake"

        def __init__(self):
            self.calls = []

        def run(self, role, payload, response_type):
            self.calls.append(role)
            raise AgentRuntimeError("deliberate failure")

    runner = FailingRunner()
    with pytest.raises(AgentRuntimeError, match="deliberate"):
        build_review_graph(runner, analyst=stub_analyst, critic=stub_critic).invoke({"request": request_t0})
    assert all(runner.calls.count(role) <= 1 for role in ("analyst", "critic"))


@pytest.mark.parametrize("bad_ref", [
    EvidenceRef(source_id="GOLD", line=1, quote="answer"),
    EvidenceRef(source_id="SYN_SUBMISSION", line=99, quote="bad"),
    EvidenceRef(source_id="SYN_SUBMISSION", line=1, quote="fabricated"),
    None,
])
def test_structural_verifier_rejects_invalid_evidence(request_t0, bad_ref):
    bad = Assessment(findings=[Finding(
        finding_id="bad", category="test", kind="observation", statement="Unsupported",
        evidence=[bad_ref] if bad_ref else [],
    )])
    result = verify_sources(request_t0, bad, Assessment())
    assert not result.passed and not result.valid_finding_ids


def test_duplicate_ids_are_reported_without_semantic_merge(request_t0):
    assessment = stub_analyst(request_t0, SubmissionStubRunner())
    result = verify_sources(request_t0, assessment, assessment)
    assert not result.passed and any("Duplicate" in error for error in result.errors)


def test_empty_findings_are_not_a_semantic_success(request_t0):
    graph = build_review_graph(SubmissionStubRunner(),
                               analyst=lambda req, runner: Assessment(),
                               critic=lambda req, runner: Assessment())
    result = graph.invoke({"request": request_t0})["result"]
    assert result.verification.source_level == "NO_EVIDENCE"
    assert not result.semantic_review_complete
    # Custom hooks must not be advertised as the known synthetic Stub.
    assert result.execution_mode == "custom" and result.real_llm_executed is None


def test_cli_stub_outputs_and_unimplemented_default(tmp_path, capsys):
    assert main(["submission-review", "--request", str(FIXTURE), "--runner", "stub",
                 "--output-dir", str(tmp_path / "stub")]) == 0
    report = json.loads((tmp_path / "stub/review.json").read_text())
    assert report["real_llm_executed"] is False
    assert "STRUCTURAL_CHECKS_ONLY" in (tmp_path / "stub/review.md").read_text()
    assert main(["submission-review", "--request", str(FIXTURE),
                 "--output-dir", str(tmp_path / "real")]) == 2
    assert "NotImplementedError" in capsys.readouterr().err
    assert not (tmp_path / "real").exists()


def test_cli_rejects_non_synthetic_and_contaminated_input(tmp_path, request_t0):
    raw = request_t0.model_dump(mode="json")
    raw["sources"][0]["provenance"] = "primary"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(raw))
    args = ["submission-review", "--request", str(path), "--runner", "stub",
            "--output-dir", str(tmp_path / "output")]
    assert main(args) == 2
    raw["sources"][0]["phase"] = "posthoc"
    path.write_text(json.dumps(raw))
    assert main(args) == 2
    assert not (tmp_path / "output").exists()
