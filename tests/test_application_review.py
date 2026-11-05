"""Application workflow tests never invoke a network model or send applicant data."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

import pytest
from pydantic import ValidationError

from career_ax.application_review import ApplicationDraft, preview, review_request, run_application
from career_ax.cli import main
from career_ax.models import Assessment, EvidenceRef, Finding
from career_ax.runner import AgentRunner


def sample_data():
    return {
        "application_id": "imsec-example",
        "company": "Example Securities",
        "role": "IT Developer",
        "job_posting": "Applicants should know Java, SQL, and web development.",
        "questions": [
            {"question_id": "q1", "prompt": "Explain your experience handling errors.",
             "draft": "I investigated a synthetic transaction retry issue."},
            {"question_id": "q2", "prompt": "Explain your software development strengths.",
             "draft": "I implemented a synthetic database state transition."},
        ],
    }


class FakeRunner(AgentRunner):
    mode = "fake"

    def __init__(self):
        self.calls = []
        self.lock = Lock()

    def run(self, role, payload, response_type):
        assert role in {"analyst", "critic"}
        with self.lock:
            self.calls.append((role, payload))
        submission = next(s for s in payload["sources"] if s["purpose"] == "submission")
        return Assessment(findings=[
            Finding(finding_id=f"{role}-001", category="observation", kind="observation",
                    statement="Evidence-backed observation",
                    evidence=[EvidenceRef(source_id=submission["source_id"],
                                          line=1, quote=submission["text"])])
        ])


def test_preview_contains_only_counts_hashes_no_applicant_text():
    draft = ApplicationDraft.model_validate(sample_data())
    output = preview(draft)
    assert output["external_model_executed"] is False
    assert output["normal_model_calls"] == 4
    assert output["maximum_model_calls_with_retries"] == 8
    assert len(output["questions"]) == 2
    raw = json.dumps(output)
    assert "transaction retry" not in raw
    assert "synthetic database" not in raw
    assert len(output["questions"][0]["input_sha256"]) == 64


def test_model_sees_only_one_question_and_the_original_job_text(tmp_path):
    draft = ApplicationDraft.model_validate(sample_data())
    runner = FakeRunner()
    report = run_application(draft, runner, tmp_path / "run")
    assert report["status"] == "COMPLETE"
    assert report["real_llm_executed"] is False
    assert len(runner.calls) == 4
    assert all(row["status"] == "COMPLETE" for row in report["questions"])
    assert (tmp_path / "run" / "summary.json").exists()
    overview = (tmp_path / "run" / "summary.md").read_text(encoding="utf-8")
    assert "q1/review.md" in overview and "q2/review.md" in overview
    assert "transaction retry" not in overview
    assert (tmp_path / "run" / "q1" / "review.json").exists()
    assert (tmp_path / "run" / "q2" / "review.md").exists()
    for role, payload in runner.calls:
        assert role in {"analyst", "critic"}
        assert set(payload) == {"request_id", "sources"}
        assert len(payload["sources"]) == 3
        assert {x["purpose"] for x in payload["sources"]} == {"reference", "question", "submission"}
        assert all(x["phase"] == "t0_input" for x in payload["sources"])
        assert "gold" not in json.dumps(payload).lower()
        if payload["request_id"].endswith("-q1"):
            assert "transaction retry" in json.dumps(payload)
            assert "synthetic database" not in json.dumps(payload)
        else:
            assert "synthetic database" in json.dumps(payload)
            assert "transaction retry" not in json.dumps(payload)


def test_rerun_must_not_overwrite_existing_evidence(tmp_path):
    draft = ApplicationDraft.model_validate(sample_data())
    runner = FakeRunner()
    run_application(draft, runner, tmp_path / "run")
    with pytest.raises(ValueError, match="not empty"):
        run_application(draft, runner, tmp_path / "run")
    assert len(runner.calls) == 4  # No second inference was attempted.


def test_duplicate_question_ids_and_unknown_fields_rejected():
    data = sample_data()
    data["questions"][1]["question_id"] = "q1"
    with pytest.raises(ValidationError, match="Duplicate"):
        ApplicationDraft.model_validate(data)
    data = sample_data()
    data["gold"] = {"leak": True}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ApplicationDraft.model_validate(data)


def test_cli_preview_is_offline_and_external_permission_is_two_step(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    request = private / "imsec.json"
    request.write_text(json.dumps(sample_data()), encoding="utf-8")
    monkeypatch.setattr("career_ax.cli.CodexRunner",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not call Codex")))
    assert main(["application-review", "--request", str(request), "--dry-run"]) == 0
    assert "external_model_executed" in capsys.readouterr().out
    assert main(["application-review", "--request", str(request),
                 "--output-dir", "runs/imsec"]) == 2
    assert "BOTH" in capsys.readouterr().err
    assert main(["application-review", "--request", str(request),
                 "--allow-external-model", "--output-dir", "runs/imsec"]) == 2
    assert "BOTH" in capsys.readouterr().err
    assert not (tmp_path / "runs" / "imsec").exists()


def test_cli_blocks_unignored_input_and_output_before_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    unsafe = tmp_path / "application.json"
    unsafe.write_text(json.dumps(sample_data()), encoding="utf-8")
    assert main(["application-review", "--request", str(unsafe), "--dry-run"]) == 2
    assert "private/" in capsys.readouterr().err
    private = tmp_path / "private"
    private.mkdir()
    request = private / "application.json"
    request.write_text(json.dumps(sample_data()), encoding="utf-8")
    assert main(["application-review", "--request", str(request),
                 "--allow-external-model", "--allow-private-input",
                 "--output-dir", "unsafe-output"]) == 2
    assert "runs/" in capsys.readouterr().err


def test_cli_can_complete_using_fake_model_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    request = private / "application.json"
    request.write_text(json.dumps(sample_data()), encoding="utf-8")
    fake = FakeRunner()
    monkeypatch.setattr("career_ax.cli.CodexRunner", lambda **kwargs: fake)
    assert main(["application-review", "--request", str(request),
                 "--allow-external-model", "--allow-private-input",
                 "--output-dir", "runs/private-test"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["question_count"] == 2
    assert response["status"] == "COMPLETE"
    assert response["real_llm_executed"] is False
    assert (tmp_path / "runs" / "private-test" / "summary.json").exists()


def test_application_input_size_is_bounded():
    data = sample_data()
    data["questions"][0]["draft"] = "x" * 12001
    with pytest.raises(ValidationError):
        ApplicationDraft.model_validate(data)
    data = sample_data()
    data["job_posting"] = "x" * 12001
    with pytest.raises(ValidationError):
        ApplicationDraft.model_validate(data)
