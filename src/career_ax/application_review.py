"""Local, bounded multi-question application review; no inference during preview."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .models import StrictModel
from .submission_review import ReviewRequest, T0Source, build_review_graph, markdown_review


class DraftQuestion(StrictModel):
    question_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    prompt: str = Field(min_length=10)
    draft: str = Field(min_length=10)


class ApplicationDraft(StrictModel):
    application_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    company: str = Field(min_length=1)
    role: str = Field(min_length=1)
    job_posting: str = Field(min_length=10)
    questions: list[DraftQuestion] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_question_ids(self):
        ids = [q.question_id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question_id")
        return self


def review_request(draft: ApplicationDraft, question: DraftQuestion) -> ReviewRequest:
    """Only JD, original prompt and draft are supplied, not Gold, prior reviews or corrections."""
    return ReviewRequest(
        request_id=f"{draft.application_id}-{question.question_id}",
        sources=(
            T0Source(source_id="JOB_POSTING", purpose="reference", phase="t0_input",
                     provenance="primary", text=draft.job_posting),
            T0Source(source_id="ORIGINAL_QUESTION", purpose="question", phase="t0_input",
                     provenance="primary", text=question.prompt),
            T0Source(source_id="DRAFT_SUBMISSION", purpose="submission", phase="t0_input",
                     provenance="primary", text=question.draft),
        ),
    )


def preview(draft: ApplicationDraft) -> dict[str, Any]:
    """Safe metadata only; prints no applicant text, source quotes or source file paths."""
    return {
        "application_id": draft.application_id,
        "question_count": len(draft.questions),
        "maximum_model_calls_with_retries": len(draft.questions) * 4,
        "normal_model_calls": len(draft.questions) * 2,
        "external_model_executed": False,
        "questions": [
            {"question_id": q.question_id, "prompt_characters": len(q.prompt),
             "draft_characters": len(q.draft),
             "input_sha256": review_request(draft, q).input_sha256}
            for q in draft.questions
        ],
        "notice": "Preview only. Review inputs yourself and redact identifying data before model execution.",
    }


def run_application(draft: ApplicationDraft, runner, output_dir: Path) -> dict[str, Any]:
    """One independent two-role graph per question. Preserve source and per-role reports."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Output directory is not empty; choose a new run directory to protect evidence")
    output_dir.mkdir(parents=True, exist_ok=True)
    graph = build_review_graph(runner)
    rows = []
    for question in draft.questions:
        request = review_request(draft, question)
        result = graph.invoke({"request": request})["result"]
        folder = output_dir / question.question_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "review.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (folder / "review.md").write_text(markdown_review(result), encoding="utf-8")
        rows.append({
            "question_id": question.question_id,
            "request_id": request.request_id,
            "input_sha256": request.input_sha256,
            "status": result.status,
            "successful_roles": result.successful_roles,
            "failures": result.failures,
            "structural_checks_passed": result.verification.passed,
            "analyst_findings": len(result.analyst.findings),
            "critic_findings": len(result.critic.findings),
            "integrated_findings": len(result.integrated.findings),
            "review_json": str(folder / "review.json"),
            "review_markdown": str(folder / "review.md"),
        })
    status = "COMPLETE" if all(row["status"] == "COMPLETE" for row in rows) else "NEEDS_HUMAN_REVIEW"
    report = {
        "application_id": draft.application_id,
        "execution_mode": runner.mode,
        "status": status,
        "real_llm_executed": runner.mode == "codex" and all(
            row["successful_roles"] for row in rows),
        "question_count": len(rows),
        "questions": rows,
        "limits": [
            "COMPLETE means model calls and structural checks succeeded, not that the draft is safe to submit.",
            "Findings may overlap, be irrelevant, or be wrong; humans must inspect every claim against the full draft.",
            "No Gold labels or past AI output were sent to either role.",
            "Actual executed Codex model and billing/usage are not independently verified.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
