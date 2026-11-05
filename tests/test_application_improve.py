"""Automated V1 triage and V2 editing tests: entirely fake, no external model."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_ax.application_review import ApplicationDraft, review_request
from career_ax.application_improve import (
    EditProposal, TriageItem, _validate_proposal, improve_application,
    load_reviewed_application, preview_improvement,
)
from career_ax.cli import main
from career_ax.models import Assessment, EvidenceRef, Finding
from career_ax.runner import AgentRunner, _codex_strict_output_schema


def sample():
    return ApplicationDraft.model_validate({
        "application_id": "synthetic_app_v1",
        "company": "Example Securities",
        "role": "IT",
        "job_posting": "Technical IT application role with database and Java skills.",
        "questions": [
            {"question_id": "q1", "prompt": "Tell us a real engineering example and your role.",
             "draft": "I made a synthetic checklist and checked it every day."},
            {"question_id": "q2", "prompt": "Describe an application security challenge you overcame.",
             "draft": "I checked synthetic SQL statements to avoid unintended updates."},
        ],
    })


def make_reviews(draft, path):
    for q in draft.questions:
        req = review_request(draft, q)
        f = {
            "finding_id": "repeated-001",
            "category": "missing_evidence",
            "kind": "observation",
            "statement": "Evidence should be clearer",
            "limits": "Not proof of failure",
            "evidence": [{
                "source_id": "DRAFT_SUBMISSION", "line": 1, "quote": q.draft
            }],
        }
        row = {
            "request_id": req.request_id,
            "input_sha256": req.input_sha256,
            "execution_mode": "codex",
            "real_llm_executed": True,
            "status": "COMPLETE",
            "successful_roles": ["analyst", "critic"],
            "failures": {},
            "verification": {"passed": True},
            "analyst": {"findings": [f]},
            "critic": {"findings": [f]},
        }
        folder = path / q.question_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "review.json").write_text(json.dumps(row), encoding="utf-8")


class FakeEditor(AgentRunner):
    mode = "fake"

    def __init__(self):
        self.calls = []

    def run(self, role, payload, response_type):
        self.calls.append(role)
        if role == "editor":
            refs = [x["finding_ref"] for x in payload["review_findings"]]
            return EditProposal(
                items=[
                    TriageItem(
                        finding_ref=ref, group_id="same_problem",
                        disposition="actionable" if i == 0 else "duplicate",
                        priority="high" if i == 0 else "low",
                        reason="Original explanation not specific enough",
                    )
                    for i, ref in enumerate(refs)
                ],
                revised_draft=payload["original_draft"] + " I explained the process.",
                edit_summary=["Made the explanation more specific"],
                facts_to_verify=["Check exact contribution"],
            )
        source = next(x for x in payload["sources"] if x["purpose"] == "submission")
        return Assessment(findings=[
            Finding(finding_id=f"{role}-1", kind="observation", category="coverage",
                    statement="Draft covers example",
                    evidence=[EvidenceRef(source_id=source["source_id"], line=1,
                                          quote=source["text"][:30])])
        ])


def test_preview_is_offline_and_uses_validated_v1(tmp_path):
    draft = sample()
    review_dir = tmp_path / "review"
    make_reviews(draft, review_dir)
    preview = preview_improvement(draft, review_dir)
    assert preview["review_finding_count"] == 4
    assert preview["normal_calls_editor_only"] == 2
    assert preview["normal_calls_with_recheck"] == 6
    assert preview["maximum_calls_with_retries"] == 14
    assert "synthetic checklist" not in json.dumps(preview)


def test_auto_merge_and_v2_generation_with_optional_recheck(tmp_path):
    draft = sample()
    review_dir = tmp_path / "review"
    make_reviews(draft, review_dir)
    runner = FakeEditor()
    report = improve_application(
        draft, review_dir, runner, tmp_path / "output",
        tmp_path / "private" / "application-v2.json", recheck_runner=runner,
    )
    assert report["original_findings"] == 4
    assert report["classified_findings"] == 4
    assert report["unique_issue_groups"] == 2
    assert report["recheck_status"] == "COMPLETE"
    assert runner.calls.count("editor") == 2
    assert runner.calls.count("analyst") == 2
    assert runner.calls.count("critic") == 2
    v2 = ApplicationDraft.model_validate_json(
        (tmp_path / "private" / "application-v2.json").read_text()
    )
    assert v2.application_id == "synthetic_app_v2"
    assert all(q.draft.endswith("I explained the process.") for q in v2.questions)
    assert (tmp_path / "output" / "recheck" / "q1" / "review.json").exists()
    assert "AI가 제안한 분류" in (tmp_path / "output" / "improvement.md").read_text()
    assert "same_problem" in (tmp_path / "output" / "improvement.md").read_text()


def test_bad_review_hash_blocks_before_calls(tmp_path):
    draft = sample()
    review_dir = tmp_path / "review"
    make_reviews(draft, review_dir)
    path = review_dir / "q2" / "review.json"
    review = json.loads(path.read_text())
    review["input_sha256"] = "tampered"
    path.write_text(json.dumps(review))
    runner = FakeEditor()
    with pytest.raises(ValueError, match="Stale"):
        improve_application(draft, review_dir, runner, tmp_path / "output",
                            tmp_path / "private" / "v2.json")
    assert not runner.calls


def test_failed_role_blocks_auto_edit(tmp_path):
    draft = sample()
    review_dir = tmp_path / "review"
    make_reviews(draft, review_dir)
    path = review_dir / "q1" / "review.json"
    review = json.loads(path.read_text())
    review["successful_roles"] = ["analyst"]
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="not fully validated"):
        load_reviewed_application(draft, review_dir)


def test_missing_or_unrecognized_finding_classification_rejected():
    from career_ax.application_improve import _validate_proposal
    draft = sample()
    question = draft.questions[0]
    a = {
        "analyst:one": {},
        "critic:two": {},
    }
    bad = EditProposal(items=[
        TriageItem(finding_ref="analyst:one", group_id="g1",
                   disposition="actionable", priority="high", reason="Fine")
    ], revised_draft="Edited synthetic draft.", edit_summary=[], facts_to_verify=[])
    with pytest.raises(ValueError, match="classify every Finding"):
        _validate_proposal(bad, a, question)
    bad.items.append(TriageItem(finding_ref="analyst:one", group_id="g1",
                                disposition="duplicate", priority="low", reason="Duplicate"))
    with pytest.raises(ValueError):
        _validate_proposal(bad, a, question)


def test_new_numbers_flagged_and_model_cannot_write_overlimit():
    from pydantic import ValidationError
    original = sample().questions[0]
    item = TriageItem(finding_ref="analyst:a", group_id="g",
                      disposition="actionable", priority="high", reason="x")
    p = EditProposal(items=[item], revised_draft="New improvement 50% is not verified",
                     edit_summary=[], facts_to_verify=[])
    flags = _validate_proposal(p, {"analyst:a": {}}, original)
    assert any("50%" in x for x in flags)
    with pytest.raises(ValidationError):
        EditProposal(items=[], revised_draft="x" * 2001,
                     edit_summary=[], facts_to_verify=[])


def test_existing_output_does_not_allow_any_new_model_call(tmp_path):
    draft = sample()
    review_dir = tmp_path / "review"
    make_reviews(draft, review_dir)
    output = tmp_path / "output"
    output.mkdir()
    (output / "keep.txt").write_text("important")
    runner = FakeEditor()
    with pytest.raises(ValueError, match="not empty"):
        improve_application(draft, review_dir, runner, output, tmp_path / "private" / "v2.json")
    assert runner.calls == []
    assert (output / "keep.txt").read_text() == "important"


def test_editor_schema_is_codex_strict():
    s = _codex_strict_output_schema(EditProposal)
    assert s["required"] == list(s["properties"])
    assert s["additionalProperties"] is False
    assert s["$defs"]["TriageItem"]["required"] == list(s["$defs"]["TriageItem"]["properties"])


def test_improve_cli_dry_run_and_consent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    draft = sample()
    review_dir = tmp_path / "runs" / "v1"
    make_reviews(draft, review_dir)
    req = tmp_path / "private" / "v1.json"
    req.parent.mkdir()
    req.write_text(draft.model_dump_json())
    base = [
        "application-improve", "--request", str(req),
        "--review-dir", "runs/v1",
        "--output-dir", "runs/improved",
        "--v2-request", "private/v2.json",
    ]
    monkeypatch.setattr("career_ax.cli.CodexRunner",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("external call")))
    assert main(base + ["--dry-run"]) == 0
    assert '"review_finding_count": 4' in capsys.readouterr().out
    assert main(base) == 2
    assert "BOTH" in capsys.readouterr().err
    assert not (tmp_path / "runs" / "improved").exists()


def test_improve_cli_rejects_path_outside_private_or_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    wrong = tmp_path / "draft.json"
    wrong.write_text(sample().model_dump_json())
    args = ["application-improve", "--request", str(wrong),
            "--review-dir", "runs/v1", "--output-dir", "runs/new",
            "--v2-request", "private/v2.json", "--dry-run"]
    assert main(args) == 2
    assert "private/" in capsys.readouterr().err
