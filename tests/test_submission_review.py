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
    assert not result.semantic_review_complete and result.integration_policy == "conservative_exact_match"
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
    assert result.execution_mode == "fake" and result.real_llm_executed is False


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


def test_roles_and_failure_policy(request_t0):
    result = build_review_graph(SubmissionStubRunner()).invoke({"request": request_t0})["result"]
    assert len(result.analyst.findings) == len(result.critic.findings) == 1
    assert decide_failure_action(TimeoutError(), role="analyst", attempt=1) == "retry"
    assert decide_failure_action(TimeoutError(), role="analyst", attempt=2) == "human_review"
    assert decide_failure_action(ValueError(), role="analyst", attempt=1) == "abort"


def test_runner_failure_preserved_without_retry(request_t0):
    class FailingRunner(AgentRunner):
        mode = "fake"
        def __init__(self): self.calls = []
        def run(self, role, payload, response_type):
            self.calls.append(role)
            raise AgentRuntimeError("deliberate failure")
    runner = FailingRunner()
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert result.status == "HUMAN_REVIEW"
    assert len(result.failures) == 2 and len(runner.calls) == 2


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
    assert "--allow-external-model required" in capsys.readouterr().err
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


class FakeRunner(AgentRunner):
    mode = "fake"
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []
    def run(self, role, payload, response_type):
        self.calls.append((role, deepcopy(payload)))
        value = self.outputs[role]
        if isinstance(value, Exception): raise value
        return response_type.model_validate(value)


def finding(request, role, statement="A claim", quote=None):
    source = next(s for s in request.sources if s.purpose == "submission")
    return {"finding_id": role + "_1", "category": "uncertain_claim", "kind": "uncertain",
            "statement": statement, "evidence": [{"source_id": source.source_id, "line": 1,
            "quote": quote if quote is not None else source.text.splitlines()[0]}]}


def test_real_role_functions_have_identical_isolated_inputs(request_t0):
    runner = FakeRunner({r: {"findings": [finding(request_t0, r)]} for r in ("analyst", "critic")})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert runner.calls[0][1] == runner.calls[1][1] == request_t0.model_dump(mode="json")
    assert result.status == "COMPLETE" and result.real_llm_executed is False
    assert result.relationships[0]["relation"] == "duplicate"
    assert len(result.integrated.findings) == 1
    assert not result.semantic_review_complete


def test_disagreement_and_false_citation(request_t0):
    runner = FakeRunner({"analyst": {"findings": [finding(request_t0, "analyst")]},
                         "critic": {"findings": [finding(request_t0, "critic", "Opposite claim")]}})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert result.status == "HUMAN_REVIEW"
    assert result.relationships[0]["relation"] == "potential_conflict"
    runner.outputs["critic"] = {"findings": [finding(request_t0, "critic", quote="FABRICATED")]}
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert not result.verification.passed
    assert [f.finding_id for f in result.integrated.findings] == ["analyst_1"]


def test_timeout_is_bounded_and_partial(request_t0):
    runner = FakeRunner({"analyst": TimeoutError(), "critic": {"findings": [finding(request_t0, "critic")]}})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert result.status == "PARTIAL_COMPLETE"
    assert [r for r, _ in runner.calls].count("analyst") == 2
    assert result.successful_roles == ["critic"]


def test_schema_failure_is_not_retried(request_t0):
    runner = FakeRunner({"analyst": {"findings": [{"garbage": True}]}, "critic": {}})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert result.failures == {"analyst": "ValidationError"}
    assert len(runner.calls) == 2


def test_all_duplicate_ids_excluded(request_t0):
    item = finding(request_t0, "analyst")
    assessment = Assessment.model_validate({"findings": [item, item]})
    verification = verify_sources(request_t0, assessment, Assessment())
    assert not verification.valid_finding_ids
    assert not reconcile_assessments(request_t0, assessment, Assessment(), verification).findings


def test_external_consent_and_missing_auth(monkeypatch):
    from career_ax.runner import CodexRunner, OpenAIRunner
    for runner in (CodexRunner, OpenAIRunner):
        with pytest.raises(AgentRuntimeError, match="allow-external"):
            runner()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AgentRuntimeError, match="OPENAI_API_KEY"):
        OpenAIRunner(allow_external=True)


def test_openai_stateless_transport_and_json(monkeypatch, request_t0):
    import io
    import urllib.request
    from career_ax.runner import OpenAIRunner
    requests = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-token")
    def send(request, timeout):
        body = json.loads(request.data)
        requests.append(body)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"findings": [], "unresolved": ["uncertain"]}'}}]}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", send)
    runner = OpenAIRunner(allow_external=True)
    for role in ("analyst", "critic"):
        runner.run(role, request_t0.model_dump(mode="json"), Assessment)
    assert len(requests) == 2
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert all(len(r["messages"]) == 2 for r in requests)
    assert requests[0]["messages"][0] != requests[1]["messages"][0]


@pytest.mark.parametrize("code,retry", [(429, True), (503, True), (401, False)])
def test_openai_http_classification(monkeypatch, request_t0, code, retry):
    import urllib.request
    import urllib.error
    from career_ax.runner import OpenAIRunner, TransientModelError
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-token")
    def send(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.openai.com", code, "error", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", send)
    with pytest.raises(AgentRuntimeError) as caught:
        OpenAIRunner(allow_external=True).run("analyst", request_t0.model_dump(mode="json"), Assessment)
    assert isinstance(caught.value, TransientModelError) == retry


def test_openai_invalid_json_rejected(monkeypatch, request_t0):
    import io
    import urllib.request
    from career_ax.runner import OpenAIRunner
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-token")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "not JSON"}}]}).encode()))
    with pytest.raises(ValidationError):
        OpenAIRunner(allow_external=True).run("critic", request_t0.model_dump(mode="json"), Assessment)


def test_distinct_findings_and_no_citation_retry(request_t0):
    a, c = finding(request_t0, "analyst"), finding(request_t0, "critic", quote="invented")
    c["category"] = "omission"
    runner = FakeRunner({"analyst": {"findings": [a]}, "critic": {"findings": [c]}})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert len(runner.calls) == 2
    assert {x["relation"] for x in result.relationships} == {"distinct"}
    assert result.status == "HUMAN_REVIEW"


def test_transient_failure_recovers_within_budget(request_t0):
    from career_ax.runner import TransientModelError
    class RecoveringRunner(FakeRunner):
        def run(self, role, payload, response_type):
            if role == "analyst" and not any(r == role for r, _ in self.calls):
                self.calls.append((role, deepcopy(payload)))
                raise TransientModelError("temporary")
            return super().run(role, payload, response_type)
    runner = RecoveringRunner({r: {"findings": [finding(request_t0, r)]} for r in ("analyst", "critic")})
    result = build_review_graph(runner).invoke({"request": request_t0})["result"]
    assert result.status == "COMPLETE" and len(runner.calls) == 3
