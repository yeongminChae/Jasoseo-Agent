"""One-run historical evidence intervention for a private job application.

This is a candidate-generation and model-comparison experiment, NOT a Gold Eval,
fact attestation, or automatic approval of a job application.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from .application_review import ApplicationDraft
from .models import StrictModel


class HistoryIssue(StrictModel):
    question_id: str
    gate_id: str
    original_quote: str
    risk: str
    severity: Literal["high", "medium", "low"]
    suggested_change: str


class HistoryAudit(StrictModel):
    issues: list[HistoryIssue]
    summary: str


class RevisedQuestion(StrictModel):
    question_id: str
    draft: str = Field(min_length=10, max_length=600)
    changes: list[str]
    facts_to_verify: list[str]


class Revision(StrictModel):
    questions: list[RevisedQuestion]
    summary: str


class BlindChoice(StrictModel):
    question_id: str
    preferred: Literal["A", "B", "TIE", "INCONCLUSIVE"]
    reason: str
    risks_a: list[str]
    risks_b: list[str]


class BlindComparison(StrictModel):
    questions: list[BlindChoice]
    summary: str


class FinalGate(StrictModel):
    question_id: str
    decision: Literal["GO", "REVISE", "UNKNOWN"]
    material_risks: list[str]
    human_fact_checks: list[str]


class FinalReview(StrictModel):
    questions: list[FinalGate]
    summary: str


def _json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return obj


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids(questions, expected) -> None:
    observed = [q.question_id for q in questions]
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise ValueError(f"Questions must match original exactly: expected={expected}, got={observed}")


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![a-zA-Z가-힣])\d+(?:[.,]\d+)*%?", text))


def preview(draft: ApplicationDraft, history: dict, *, request_sha: str, history_sha: str) -> dict:
    if not history.get("history_gates") or not history.get("career_boundaries"):
        raise ValueError("History packet requires history_gates and career_boundaries")
    gates = history["history_gates"].get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("History packet must contain nonempty gates")
    if not history.get("historical_examples"):
        raise ValueError("Historical examples missing; this is not a history-based run")
    return {
        "application_id": draft.application_id,
        "questions": [q.question_id for q in draft.questions],
        "question_count": len(draft.questions),
        "history_gate_count": len(gates),
        "request_sha256": request_sha,
        "history_sha256": history_sha,
        "model_calls": 4,
        "external_model_executed": False,
        "quality_eval": "model-based blinded A/B preference, NOT human Gold",
    }


def run(draft: ApplicationDraft, history: dict, output_dir: Path, runner, *,
        request_sha: str, history_sha: str) -> dict:
    """Audit -> edit -> blinded compare -> fresh final gate, preserving all outputs."""
    metadata = preview(draft, history, request_sha=request_sha, history_sha=history_sha)
    if output_dir.exists():
        raise ValueError("Output directory already exists; never overwrite earlier evidence")
    output_dir.mkdir(parents=True, exist_ok=False)
    _write(output_dir / "input_snapshot.json", metadata)

    original_questions = [q.model_dump() for q in draft.questions]
    expected = [q.question_id for q in draft.questions]
    gate_ids = {g["id"] for g in history["history_gates"]["gates"]}

    audit = HistoryAudit.model_validate(runner.run("historical_auditor", {
        "company": draft.company, "role": draft.role,
        "job_posting": draft.job_posting,
        "questions": original_questions,
        "history_gates": history["history_gates"],
        "historical_examples": history["historical_examples"],
        "career_boundaries": history["career_boundaries"],
        "task": "Find specific repeat historical mistakes; quote current draft verbatim. "
                "Do not claim prior hiring rejection was caused by an essay defect.",
    }, HistoryAudit).model_dump())
    for issue in audit.issues:
        question = next((q for q in draft.questions if q.question_id == issue.question_id), None)
        if question is None or issue.gate_id not in gate_ids or issue.original_quote not in question.draft:
            raise ValueError("Historical audit contains unknown gate/question or nonverbatim quote")
    _write(output_dir / "01_historical_audit.json", audit.model_dump())

    revision = Revision.model_validate(runner.run("historical_editor", {
        "company": draft.company, "role": draft.role,
        "job_posting": draft.job_posting,
        "questions": original_questions,
        "historical_audit": audit.model_dump(),
        "history_gates": history["history_gates"],
        "career_boundaries": history["career_boundaries"],
        "task": "Solve historical failures, not paraphrase for its own sake. "
                "Keep unchanged answers when no demonstrated improvement exists. "
                "Do not fabricate personal motivations, company activities, experience, metrics, "
                "or actual pre-fix failures. Each Korean answer <=600 characters.",
    }, Revision).model_dump())
    _ids(revision.questions, expected)
    fact_text = json.dumps(history["career_boundaries"], ensure_ascii=False)
    numeric_allow = _numbers(draft.job_posting + fact_text + "\n".join(q.draft for q in draft.questions))
    unexpected = {}
    for q in revision.questions:
        new = sorted(_numbers(q.draft) - numeric_allow)
        if new:
            unexpected[q.question_id] = new
    if unexpected:
        raise ValueError(f"Editor introduced unsupported numeric tokens: {unexpected}")
    _write(output_dir / "02_history_revision.json", revision.model_dump())

    revisions = {q.question_id: q for q in revision.questions}
    assignments = {}
    blind_items = []
    for q in draft.questions:
        revised = revisions[q.question_id].draft
        swap = int(hashlib.sha256((draft.application_id + ":" + q.question_id).encode()).hexdigest(), 16) % 2 == 1
        a, b = (revised, q.draft) if swap else (q.draft, revised)
        assignments[q.question_id] = {"A": "revised" if swap else "original",
                                      "B": "original" if swap else "revised"}
        blind_items.append({"question_id": q.question_id, "question": q.prompt,
                            "A": a, "B": b, "character_limit": 600})

    comparison = BlindComparison.model_validate(runner.run("historical_comparator", {
        "company": draft.company, "role": draft.role, "job_posting": draft.job_posting,
        "history_gates": history["history_gates"],
        "pairs": blind_items,
        "task": "Blind A/B: judge direct question fit, evidence, specificity, company fit, "
                "role boundaries and unsupported claims. Never infer which is rewritten. "
                "Prefer TIE/INCONCLUSIVE where evidence is insufficient.",
    }, BlindComparison).model_dump())
    _ids(comparison.questions, expected)
    _write(output_dir / "03_blind_comparison.json", comparison.model_dump())

    choices = {x.question_id: x for x in comparison.questions}
    selected = []
    decisions = []
    for q in draft.questions:
        c = choices[q.question_id]
        choice = assignments[q.question_id].get(c.preferred, "original")
        use_revision = choice == "revised" and revisions[q.question_id].draft != q.draft
        candidate = revisions[q.question_id].draft if use_revision else q.draft
        selected.append(q.model_copy(update={"draft": candidate}))
        decisions.append({"question_id": q.question_id, "preferred": c.preferred,
                          "selected": "revised" if use_revision else "original",
                          "reason": c.reason, "changes": revisions[q.question_id].changes,
                          "facts_to_verify": revisions[q.question_id].facts_to_verify})

    final = draft.model_copy(update={
        "application_id": draft.application_id.removesuffix("_v1") + "_history_candidate",
        "questions": selected,
    })
    final = ApplicationDraft.model_validate(final.model_dump())

    risk_check = FinalReview.model_validate(runner.run("historical_final_reviewer", {
        "company": final.company, "role": final.role, "job_posting": final.job_posting,
        "questions": [q.model_dump() for q in final.questions],
        "history_gates": history["history_gates"],
        "career_boundaries": history["career_boundaries"],
        "task": "Fresh independent red-team of selected essays. Recheck fabricated facts, "
                "question fit and company-specific reasons. No prior audit, editor or A/B "
                "conclusions are available; report UNKNOWN on unsupported material claims. "
                "GO means no detected textual blocker, NOT verified facts or hiring prediction.",
    }, FinalReview).model_dump())
    _ids(risk_check.questions, expected)
    _write(output_dir / "04_final_redteam.json", risk_check.model_dump())

    status = ("REVISE_OR_UNKNOWN" if any(q.decision != "GO" for q in risk_check.questions)
              else "CANDIDATE_HUMAN_FACT_CHECK_REQUIRED")
    report = {
        "status": status, "application_id": final.application_id,
        "source_request_sha256": request_sha, "source_history_sha256": history_sha,
        "all_four_codex_roles_completed": True, "independent_gold_eval": False,
        "questions": [{**row,
                       "redteam_status": next(f.decision for f in risk_check.questions if f.question_id == row["question_id"]),
                       "redteam_risks": next(f.material_risks for f in risk_check.questions if f.question_id == row["question_id"]),
                       "redteam_fact_checks": next(f.human_fact_checks for f in risk_check.questions if f.question_id == row["question_id"])}
                      for row in decisions],
        "warnings": [
            "Historical cases are not independently adjudicated Gold labels.",
            "Model-based A/B preference does not prove improvement or predict hiring results.",
            "Employer experience and personal company motivation require applicant confirmation.",
            "No recruiter submission is made by this command.",
        ],
    }
    _write(output_dir / "candidate.json", final.model_dump())
    _write(output_dir / "summary.json", report)
    lines = [f"# {draft.company} 역사 자료 개입 실험", "",
             f"- Status: **{status}**",
             f"- Input SHA256: `{request_sha}`",
             f"- History SHA256: `{history_sha}`",
             "- Four Codex roles completed. **Not a human Gold Eval**.", ""]
    for row in report["questions"]:
        chosen = next(q for q in final.questions if q.question_id == row["question_id"])
        lines += [f"## {row['question_id']}: {row['selected']} (A/B: {row['preferred']})",
                  row["reason"], f"Final red-team: {row['redteam_status']}",
                  "", chosen.draft, "",
                  "Remaining checks: " + "; ".join(row["redteam_risks"] + row["redteam_fact_checks"] + row["facts_to_verify"]), ""]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return report
