"""Offline Eval unit tests use fabricated model outputs, not live inference."""
from copy import deepcopy
import json

import pytest

from career_ax.eval_submission import EvaluationError, _digest, evaluate, prepare


def case():
    request = {"request_id": "synthetic-test", "sources": [
        {"source_id": "Q", "purpose": "question", "phase": "t0_input", "provenance": "synthetic", "text": "원인과 검증 결과를 쓰세요."},
        {"source_id": "S", "purpose": "submission", "phase": "t0_input", "provenance": "synthetic", "text": "공동 작업표를 만들었습니다."},
    ]}
    gold = {"request_id": "synthetic-test", "evaluation_only": True,
            "expected_defects": [{"defect_id": "G_CAUSE"}, {"defect_id": "G_VALIDATION"}]}
    review = {"request_id": "synthetic-test", "input_sha256": _digest(request),
              "execution_mode": "codex", "real_llm_executed": True, "status": "COMPLETE",
              "successful_roles": ["analyst", "critic"], "failures": {}, "verification": {"passed": True},
              "analyst": {"findings": [{"finding_id": "analyst_1", "statement": "원인 누락"}]},
              "critic": {"findings": [{"finding_id": "critic_1", "statement": "검증 결과 누락"}]}}
    judgments = {"request_id": "synthetic-test", "input_sha256": _digest(request), "judgments": [
        {"role": "analyst", "finding_id": "analyst_1", "verdict": "match", "gold_defect_id": "G_CAUSE", "reason": "원문에 지연 원인 없음"},
        {"role": "critic", "finding_id": "critic_1", "verdict": "match", "gold_defect_id": "G_VALIDATION", "reason": "효과 검증 기록 없음"},
    ]}
    return request, gold, review, judgments


def test_independent_analyst_vs_combined_gold_coverage():
    request, gold, review, judgments = case()
    result = evaluate(review, request, gold, judgments)
    assert result["analyst"]["tp_gold"] == 1
    assert result["analyst"]["fn_gold"] == 1
    assert result["combined"]["tp_gold"] == 2
    assert result["combined"]["fn_gold"] == 0
    assert result["combined"]["gold_recall"] == 1


def test_no_findings_on_clean_case_metrics_are_not_invented():
    request, gold, review, judgments = case()
    gold["expected_defects"] = []
    review["analyst"]["findings"] = []
    review["critic"]["findings"] = []
    judgments["judgments"] = []
    report = evaluate(review, request, gold, judgments)
    assert report["combined"]["gold_recall"] is None
    assert report["combined"]["finding_precision"] is None
    assert report["combined"]["false_positive_findings"] == 0


def test_duplicate_gold_matches_count_once_and_false_positive():
    request, gold, review, judgments = case()
    judgments["judgments"][1]["gold_defect_id"] = "G_CAUSE"
    result = evaluate(review, request, gold, judgments)
    assert result["combined"]["tp_gold"] == 1
    assert result["combined"]["gold_recall"] == 0.5
    judgments["judgments"][1].update(verdict="false_positive", gold_defect_id=None)
    result = evaluate(review, request, gold, judgments)
    assert result["combined"]["false_positive_findings"] == 1
    assert result["combined"]["finding_precision"] == .5


@pytest.mark.parametrize("mutation", [
    lambda q,g,r,j: r.update(real_llm_executed=False),
    lambda q,g,r,j: r.update(execution_mode="stub"),
    lambda q,g,r,j: r.update(successful_roles=["analyst"]),
    lambda q,g,r,j: r["verification"].update(passed=False),
    lambda q,g,r,j: r.update(input_sha256="wrong"),
    lambda q,g,r,j: j["judgments"].pop(),
    lambda q,g,r,j: j["judgments"][0].update(verdict=None),
    lambda q,g,r,j: j["judgments"][0].update(gold_defect_id="NONEXISTENT"),
    lambda q,g,r,j: j["judgments"][0].update(reason=""),
    lambda q,g,r,j: g.update(evaluation_only=False),
])
def test_rejects_unverified_stale_or_unjudged(mutation):
    q, g, r, j = case()
    mutation(q, g, r, j)
    with pytest.raises(EvaluationError):
        evaluate(r, q, g, j)


def test_prepare_has_no_auto_gold_or_judgment():
    _, _, r, _ = case()
    template = prepare(r)
    assert len(template["judgments"]) == 2
    assert all(x["verdict"] is None and x["gold_defect_id"] is None for x in template["judgments"])


def test_three_gold_fixtures_are_isolated():
    from pathlib import Path
    from career_ax.submission_review import ReviewRequest
    root = Path(__file__).resolve().parents[1] / "fixtures/submission_review"
    for folder, expected in [("missing_cause_validation", 2), ("complete_response", 0),
                             ("unsupported_quantitative", 1)]:
        request = ReviewRequest.model_validate_json((root / folder / "request.json").read_text(encoding="utf-8"))
        gold = json.loads((root / folder / "gold.json").read_text(encoding="utf-8"))
        assert gold["evaluation_only"] is True
        assert len(gold["expected_defects"]) == expected
        assert request.request_id == gold["request_id"]
        assert "gold" not in request.model_dump()
        assert all(s.provenance == "synthetic" and s.phase == "t0_input" for s in request.sources)
