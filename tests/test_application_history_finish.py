"""Regression checks for historical evidence end-to-end finishing pipeline."""
import json

import pytest

from career_ax.application_history_finish import (
    BlindChoice, BlindComparison, FinalGate, FinalReview,
    HistoryAudit, HistoryIssue, RevisedQuestion, Revision, preview, run,
)
from career_ax.application_review import ApplicationDraft


PACKET = {
    "history_gates": {"gates": [{"id": "G02", "test": "company specificity"}]},
    "historical_examples": [{"example": "anonymous reviewed essay", "tier": "B"}],
    "career_boundaries": {"known_fact": "verified project implementation"},
}


def draft():
    return ApplicationDraft.model_validate({
        "application_id": "sample_v1",
        "company": "가상기업", "role": "신입 IT",
        "job_posting": "가상의 개발 회사에서 Java 개발자를 모집합니다.",
        "questions": [
            {"question_id": "q1", "prompt": "지원 이유와 본인이 기여할 점을 설명해 주세요.",
             "draft": "좋은 회사라고 생각하여 지원했습니다. 열심히 하겠습니다."},
            {"question_id": "q2", "prompt": "가장 의미 있었던 개발 경험을 설명해 주세요.",
             "draft": "기존 시스템에서 데이터베이스 개발을 담당하고 검증했습니다."},
        ],
    })


class FakeCodex:
    def __init__(self, *, invalid_quote=False, new_number=False, unresolved=False):
        self.calls = []
        self.invalid_quote = invalid_quote
        self.new_number = new_number
        self.unresolved = unresolved

    def run(self, role, payload, response_type):
        self.calls.append(role)
        if role == "historical_auditor":
            quote = "없는 문장" if self.invalid_quote else "좋은 회사라고 생각하여"
            return HistoryAudit(issues=[HistoryIssue(question_id="q1", gate_id="G02",
                 original_quote=quote, risk="일반적 지원 이유", severity="high",
                 suggested_change="지원 이유를 확인된 사실과 연결")], summary="결함 발견")
        if role == "historical_editor":
            replacement = ("2027년에 확인된 실무를 바탕으로 기여하겠습니다."
                           if self.new_number else
                           "직접 구현한 데이터 검증 경험으로 업무의 일관성을 높이겠습니다.")
            return Revision(questions=[
                RevisedQuestion(question_id="q1", draft=replacement,
                                changes=["근거 기반 동기"], facts_to_verify=[]),
                RevisedQuestion(question_id="q2", draft=payload["questions"][1]["draft"],
                                changes=[], facts_to_verify=[]),
            ], summary="선별 수정")
        if role == "historical_comparator":
            out = []
            for item in payload["pairs"]:
                if item["question_id"] == "q1":
                    preferred = "A" if "직접 구현한" in item["A"] else "B"
                else:
                    preferred = "TIE"
                out.append(BlindChoice(question_id=item["question_id"],
                    preferred=preferred, reason="구체성 비교",
                    risks_a=[], risks_b=[]))
            return BlindComparison(questions=out, summary="비교 완료")
        if role == "historical_final_reviewer":
            return FinalReview(questions=[
                FinalGate(question_id=q["question_id"],
                          decision="UNKNOWN" if self.unresolved else "GO",
                          material_risks=[], human_fact_checks=[])
                for q in payload["questions"]
            ], summary="최종 검토")
        raise AssertionError(role)


def test_dry_run_is_offline(tmp_path):
    info = preview(draft(), PACKET, request_sha="a", history_sha="b")
    assert info["model_calls"] == 4
    assert info["history_gate_count"] == 1
    assert info["external_model_executed"] is False


def test_real_flow_preserves_original_and_blind_selects(tmp_path):
    runner = FakeCodex()
    app = draft()
    before = app.model_dump_json()
    dest = tmp_path / "output"
    report = run(app, PACKET, dest, runner, request_sha="input", history_sha="history")
    assert runner.calls == [
        "historical_auditor", "historical_editor",
        "historical_comparator", "historical_final_reviewer",
    ]
    assert report["status"] == "CANDIDATE_HUMAN_FACT_CHECK_REQUIRED"
    result = json.loads((dest / "candidate.json").read_text())
    assert result["questions"][0]["draft"].startswith("직접 구현한")
    assert result["questions"][1]["draft"] == app.questions[1].draft
    assert app.model_dump_json() == before
    assert (dest / "summary.md").exists()
    with pytest.raises(ValueError, match="already exists"):
        run(app, PACKET, dest, FakeCodex(), request_sha="a", history_sha="b")


def test_history_quote_is_exact(tmp_path):
    with pytest.raises(ValueError, match="nonverbatim"):
        run(draft(), PACKET, tmp_path / "run", FakeCodex(invalid_quote=True),
            request_sha="a", history_sha="b")
    assert not (tmp_path / "run" / "candidate.json").exists()


def test_new_unsupported_number_is_blocked(tmp_path):
    with pytest.raises(ValueError, match="unsupported numeric"):
        run(draft(), PACKET, tmp_path / "run", FakeCodex(new_number=True),
            request_sha="a", history_sha="b")


def test_redteam_unknown_blocks_go(tmp_path):
    report = run(draft(), PACKET, tmp_path / "run", FakeCodex(unresolved=True),
                 request_sha="a", history_sha="b")
    assert report["status"] == "REVISE_OR_UNKNOWN"
