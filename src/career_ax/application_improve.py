"""Automated triage and revision proposal for completed multi-question reviews.

This is an AI-proposed editing stage, NOT independent fact-checking or a hiring score.
Nothing in this module uploads to an external model without an explicitly supplied runner.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field

from .application_review import ApplicationDraft, review_request, run_application
from .models import StrictModel


class TriageItem(StrictModel):
    finding_ref: str = Field(min_length=3)
    group_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    disposition: Literal["actionable", "duplicate", "normal", "fact_check", "irrelevant"]
    priority: Literal["high", "medium", "low", "none"]
    reason: str = Field(min_length=1)


class EditProposal(StrictModel):
    items: list[TriageItem]
    revised_draft: str = Field(min_length=10, max_length=2000)
    edit_summary: list[str]
    facts_to_verify: list[str]


def _findings(review: dict) -> dict[str, dict]:
    findings = {}
    for role in ("analyst", "critic"):
        for item in review[role]["findings"]:
            key = f'{role}:{item["finding_id"]}'
            if key in findings:
                raise ValueError(f"Duplicate reviewer finding: {key}")
            findings[key] = item
    return findings


def load_reviewed_application(draft: ApplicationDraft, review_dir: Path) -> list[tuple]:
    """Never process stale, partially failed or structurally invalid V1 model responses."""
    rows = []
    for q in draft.questions:
        path = review_dir / q.question_id / "review.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        request = review_request(draft, q)
        if (review.get("request_id") != request.request_id or
                review.get("input_sha256") != request.input_sha256):
            raise ValueError(f"Stale or wrong V1 review: {q.question_id}")
        if (review.get("execution_mode") != "codex" or
                review.get("real_llm_executed") is not True or
                set(review.get("successful_roles", [])) != {"analyst", "critic"} or
                review.get("failures") or
                not review.get("verification", {}).get("passed", False)):
            raise ValueError(f"V1 review not fully validated: {q.question_id}")
        rows.append((q, review, _findings(review)))
    return rows


def preview_improvement(draft: ApplicationDraft, review_dir: Path) -> dict:
    rows = load_reviewed_application(draft, review_dir)
    return {
        "application_id": draft.application_id,
        "question_count": len(rows),
        "review_finding_count": sum(len(items) for _, _, items in rows),
        "normal_calls_editor_only": len(rows),
        "normal_calls_with_recheck": len(rows) * 3,
        "maximum_calls_with_retries": len(rows) * 7,
        "external_model_executed": False,
        "questions": [{
            "question_id": q.question_id, "finding_count": len(findings),
            "input_sha256": review["input_sha256"],
        } for q, review, findings in rows],
    }


def _numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"(?<![a-zA-Z가-힣])\d+(?:[.,]\d+)*%?", text))


def _editor_payload(draft: ApplicationDraft, q, findings: dict) -> dict:
    return {
        "company": draft.company,
        "role": draft.role,
        "job_posting": draft.job_posting,
        "question_id": q.question_id,
        "question": q.prompt,
        "original_draft": q.draft,
        "character_limit": 600,
        "review_findings": [
            {"finding_ref": ref, "kind": f["kind"], "category": f["category"],
             "statement": f["statement"], "limits": f.get("limits", ""),
             "evidence": f["evidence"]}
            for ref, f in findings.items()
        ],
        "editing_rules": [
            "Classify EVERY reviewer Finding exactly once; duplicates share a group_id",
            "Do not conflate positive/normal observations with defects",
            "Treat facts not proven by input as fact_check, not false facts",
            "Revise using only original draft statements and job posting, never invent accomplishments or metrics",
            "No implied commercial payment-network operation or securities trading experience",
            "600 characters including whitespace; preserve question fit and factual scope",
            "Use no external tools, browsing or prior conversations",
            "Call out company claims or performance figures that need original-source verification",
        ],
    }


def _validate_proposal(proposal: EditProposal, findings: dict, q) -> list[str]:
    original = set(findings)
    reported = [item.finding_ref for item in proposal.items]
    if len(reported) != len(set(reported)) or set(reported) != original:
        raise ValueError(
            f"Editor must classify every Finding exactly once: {q.question_id}; "
            f"missing={sorted(original - set(reported))}, extra={sorted(set(reported) - original)}"
        )
    if len(proposal.revised_draft) > 600 or not proposal.revised_draft.strip():
        raise ValueError(f"Revised draft exceeds 600 characters or is blank: {q.question_id}")
    new_numbers = sorted(_numeric_tokens(proposal.revised_draft) - _numeric_tokens(q.draft))
    return [f"New number/date/percentage in V2 (not in V1): {x}" for x in new_numbers]


def _summarize_groups(items: list[TriageItem]) -> list[dict]:
    groups = defaultdict(list)
    for item in items:
        groups[item.group_id].append(item)
    priority_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    return sorted([
        {
            "group_id": group,
            "finding_refs": [i.finding_ref for i in members],
            "dispositions": sorted({i.disposition for i in members}),
            "priority": min((i.priority for i in members), key=lambda p: priority_order[p]),
            "reason": members[0].reason,
        }
        for group, members in groups.items()
    ], key=lambda g: (priority_order[g["priority"]], g["group_id"]))


def _markdown(draft: ApplicationDraft, proposals: list[dict], recheck: dict | None) -> str:
    lines = [
        f"# {draft.company} 자기소개서 — 자동 중복 정리 및 V2 제안",
        "",
        "> AI가 제안한 분류와 수정안입니다. 사실 검증 완료 또는 제출 승인 결과가 아닙니다.",
        "> 원본 지원서·성과·회사 활동의 진위는 지원자 본인이 확인해야 합니다.",
        "",
    ]
    for p in proposals:
        lines += [
            f'## {p["question_id"]}',
            "",
            f'- V1 Finding {p["v1_findings"]}건 → 의미 그룹 {len(p["groups"])}개',
            f'- V2 글자 수(공백 포함): {p["revised_characters"]}/600',
            f'- 신규 수치/연도 확인 신호: {len(p["new_numeric_tokens"])}건',
            "",
            "**수정 제안**",
            "",
            p["revised_draft"],
            "",
            "**자동 분류된 검토 항목**",
            "",
        ]
        for group in p["groups"]:
            lines.append(
                f'- [{group["priority"]}] {group["group_id"]} '
                f'({", ".join(group["dispositions"])}; {", ".join(group["finding_refs"])}): '
                f'{group["reason"]}'
            )
        lines += ["", "**수정 요약**", ""]
        lines.extend(f'- {s}' for s in p["edit_summary"])
        lines += ["", "**사실 확인 대기**", ""]
        facts = list(dict.fromkeys(p["facts_to_verify"] + p["new_numeric_tokens"]))
        lines.extend(f'- {s}' for s in facts if s)
        if not facts:
            lines.append("- 추가 자동 탐지 항목 없음. 원고 전체의 사실 확인은 여전히 필요.")
        lines.append("")
    if recheck is not None:
        lines += [
            "## V2 재검토 실행 결과",
            "",
            f'- 재검토 상태: {recheck["status"]}',
            "- 재검토 결과의 Finding 개수 감소는 품질 향상 증명이 아닙니다.",
            "- 상세 V2 검토는 `recheck/summary.md` 및 문항별 보고서를 확인하세요.",
            "",
        ]
    return "\n".join(lines)


def improve_application(draft: ApplicationDraft, review_dir: Path, editor,
                        output_dir: Path, v2_request_path: Path,
                        *, recheck_runner=None) -> dict:
    """One editor call per question, optionally two independent reviewer calls for each V2.

    Input reports are checked before any model calls. Existing results and draft V1 are immutable.
    """
    rows = load_reviewed_application(draft, review_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Improvement output dir is not empty; preserve earlier runs")
    if v2_request_path.exists():
        raise ValueError("V2 request path already exists; preserve earlier drafts")

    proposals = []
    v2_questions = []
    for q, review, findings in rows:
        if not findings:
            raise ValueError(f"No reviewer findings to triage: {q.question_id}")
        output = editor.run("editor", _editor_payload(draft, q, findings), EditProposal)
        proposal = EditProposal.model_validate(output.model_dump())
        new_numeric = _validate_proposal(proposal, findings, q)
        groups = _summarize_groups(proposal.items)
        proposals.append({
            "question_id": q.question_id,
            "v1_input_sha256": review["input_sha256"],
            "v1_findings": len(findings),
            "classification_count": len(proposal.items),
            "groups": groups,
            "revised_draft": proposal.revised_draft,
            "revised_characters": len(proposal.revised_draft),
            "edit_summary": proposal.edit_summary,
            "facts_to_verify": proposal.facts_to_verify,
            "new_numeric_tokens": new_numeric,
            "status": "AI_PROPOSED_HUMAN_FACT_CHECK_REQUIRED",
        })
        v2_questions.append(q.model_copy(update={"draft": proposal.revised_draft}))

    v2 = draft.model_copy(update={
        "application_id": draft.application_id.removesuffix("_v1") + "_v2",
        "questions": v2_questions,
    })
    # Validate updated fields, and prevent possible duplicate question IDs.
    v2 = ApplicationDraft.model_validate(v2.model_dump())
    recheck = None
    output_dir.mkdir(parents=True, exist_ok=False)
    v2_request_path.parent.mkdir(parents=True, exist_ok=True)
    v2_request_path.write_text(v2.model_dump_json(indent=2) + "\n", encoding="utf-8")

    if recheck_runner is not None:
        recheck = run_application(v2, recheck_runner, output_dir / "recheck")

    report = {
        "application_id": draft.application_id,
        "status": "AI_PROPOSAL_NEEDS_HUMAN_REVIEW",
        "original_findings": sum(p["v1_findings"] for p in proposals),
        "classified_findings": sum(p["classification_count"] for p in proposals),
        "unique_issue_groups": sum(len(p["groups"]) for p in proposals),
        "v2_request_file": str(v2_request_path),
        "recheck_status": recheck["status"] if recheck else "NOT_REQUESTED",
        "questions": proposals,
        "limits": [
            "No automatic factual truth validation; candidate edits must be checked against genuine work records.",
            "New numeric tokens are flagged, not proof that unchanged numbers or company activities are true.",
            "Finding grouping is AI-proposed; duplicates may be missed or incorrectly merged.",
            "V2 review checks quotations and output schemas, not whether the prose improved.",
        ],
    }
    (output_dir / "improvement.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "improvement.md").write_text(
        _markdown(draft, proposals, recheck), encoding="utf-8"
    )
    return report
