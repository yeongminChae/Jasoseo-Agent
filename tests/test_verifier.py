from pathlib import Path
from career_ax.evidence import Registry
from career_ax.models import EvidenceRef, Collection, Assessment, Finding
from career_ax.verifier import verify,followup_is_linked

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_question_swap" / "manifest.json"

def test_reject_hallucinated_citation():
    reg=Registry(DEMO)
    good=EvidenceRef(source_id="SYN_FORM_T0",line=2,quote="2번 문항 지원동기")
    bad=EvidenceRef(source_id="SYN_FORM_T0",line=2,quote="내용에 없는 엄청난 성과")
    a=Assessment(findings=[Finding(finding_id="f1",category="test",kind="observation",statement="주장",evidence=[bad])])
    result=verify(reg,Collection(evidence=[good]),a,Assessment())
    assert result.passed is False
    assert "invalid/non-collected citation" in " ".join(result.errors)

def test_no_citation_is_error():
    reg=Registry(DEMO)
    good=EvidenceRef(source_id="SYN_FORM_T0",line=2,quote="2번 문항 지원동기")
    a=Assessment(findings=[Finding(finding_id="f1",category="test",kind="observation",statement="주장")])
    assert not verify(reg,Collection(evidence=[good]),a,Assessment()).passed

def test_followup_requires_topic_overlap():
    assert followup_is_linked("문항 순서 검토 절차", ["문항 순서 불일치 발견"])
    assert not followup_is_linked("클라우드 비용 최적화", ["문항 순서 불일치 발견"])
