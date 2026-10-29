from pathlib import Path
import pytest
from career_ax.evidence import Registry
from career_ax.models import Assessment, Finding, EvidenceRef, RunStatus, Decision, NextAction
from career_ax.runner import MockRunner
from career_ax.store import Store
from career_ax.workflow import Workflow

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_question_swap" / "manifest.json"
GOAL="가상 GPT 자기소개서 검토에서 문항 순서 오류를 분석하고 다음 검증 질문을 조사하라"

def make(tmp_path, runner=None, calls=20, cycles=3, manifest=DEMO):
    return Workflow(Registry(manifest),Store(tmp_path/"research.sqlite"),runner or MockRunner(),max_cycles=cycles,max_calls=calls)

def test_two_linked_cycles_without_user_intervention(tmp_path):
    report=make(tmp_path).execute(GOAL)
    assert report.status==RunStatus.COMPLETE
    assert report.runner=="mock" and not report.is_real_llm
    assert len(report.cycles)==2
    a,b=report.cycles
    assert b.based_on_finding_ids and set(b.based_on_finding_ids).issubset(a.accepted_finding_ids)
    assert "문항 순서" in b.question
    assert b.question != a.question
    assert a.verification.passed and b.verification.passed
    assert a.decision.action==NextAction.CONTINUE
    assert b.decision.action==NextAction.COMPLETE

def test_budget_stop_and_resume_from_saved_cycle(tmp_path):
    first=make(tmp_path,calls=5).execute(GOAL)
    assert first.status==RunStatus.BUDGET_EXHAUSTED
    assert len(first.cycles)==1
    continued=make(tmp_path,calls=20).execute(run_id=first.run_id)
    assert continued.status==RunStatus.COMPLETE
    assert len(continued.cycles)==2
    assert continued.calls_used==9

def test_missing_evidence_blocks_without_claims(tmp_path):
    folder=tmp_path/"sources"
    folder.mkdir()
    (folder/"empty.txt").write_text("완전히 관련 없는 열쇠",encoding="utf-8")
    (folder/"manifest.json").write_text('{"sources":[{"source_id":"empty","path":"empty.txt","provenance":"synthetic"}]}',encoding="utf-8")
    report=make(tmp_path,manifest=folder/"manifest.json").execute(GOAL)
    assert report.status==RunStatus.BLOCKED
    assert len(report.cycles)==0

def test_spy_inputs_are_blind_and_independent(tmp_path):
    class Spy(MockRunner):
        def __init__(self):self.calls=[]
        def run(self,role,payload,response_type):
            self.calls.append((role,dict(payload)))
            return super().run(role,payload,response_type)
    spy=Spy()
    make(tmp_path,runner=spy).execute(GOAL)
    analyst=[p for role,p in spy.calls if role=="analyst"][0]
    critic=[p for role,p in spy.calls if role=="critic"][0]
    assert analyst==critic
    assert "analyst" not in critic and "critic" not in analyst
    assert "SYN_CORRECTION_POSTHOC" not in str(critic)

def test_fabricated_agent_claim_is_not_promoted(tmp_path):
    class BadAnalyst(MockRunner):
        def run(self,role,payload,response_type):
            if role=="analyst":
                return Assessment(findings=[Finding(finding_id="fake",category="test",kind="observation",statement="사실 확인됨",evidence=[EvidenceRef(source_id="SYN_FORM_T0",line=2,quote="존재하지 않는 인용")])])
            return super().run(role,payload,response_type)
    report=make(tmp_path,runner=BadAnalyst()).execute(GOAL)
    assert report.status==RunStatus.BLOCKED
    assert len(report.cycles)==1
    assert not report.cycles[0].verification.passed
    assert report.cycles[0].accepted_finding_ids==[]

def test_cannot_resume_with_modified_input(tmp_path):
    first=make(tmp_path,calls=5).execute(GOAL)
    folder=tmp_path/"other"
    folder.mkdir()
    (folder/"file.txt").write_text("abc",encoding="utf-8")
    (folder/"manifest.json").write_text('{"sources":[{"source_id":"a","path":"file.txt","provenance":"synthetic"}]}',encoding="utf-8")
    with pytest.raises(ValueError,match="snapshot"):
        make(tmp_path,manifest=folder/"manifest.json").execute(run_id=first.run_id)

def test_reject_unrelated_lead_followup(tmp_path):
    class OffTopic(MockRunner):
        def run(self,role,payload,response_type):
            if role=="lead_next":
                return Decision(action=NextAction.CONTINUE,reason="다른 주제",next_question="클라우드 비용 최적화",based_on_finding_ids=payload["valid_finding_ids"][:1])
            return super().run(role,payload,response_type)
    report=make(tmp_path,runner=OffTopic()).execute(GOAL)
    assert report.status==RunStatus.HUMAN_REVIEW
    assert len(report.cycles)==1

def test_real_codex_not_auto_enabled():
    from career_ax.runner import CodexRunner,AgentRuntimeError
    with pytest.raises(AgentRuntimeError,match="allow-external"):
        CodexRunner(allow_external=False)

def test_failed_attempt_is_counted_and_retried_once(tmp_path):
    from career_ax.runner import AgentRuntimeError
    class Transient(MockRunner):
        def __init__(self):self.once=True
        def run(self,role,payload,response_type):
            if role=="collector" and self.once:
                self.once=False
                raise AgentRuntimeError("temporary")
            return super().run(role,payload,response_type)
    runner=Transient()
    report=make(tmp_path,runner=runner).execute(GOAL)
    assert report.status==RunStatus.COMPLETE
    assert report.calls_used==10 # 9 successes + 1 failed attempt
    cur=Store(tmp_path/"research.sqlite").connection.execute("SELECT outcome FROM agent_calls WHERE run_id=?",(report.run_id,))
    outcomes=[r[0] for r in cur.fetchall()]
    assert outcomes.count("FAILED")==1 and outcomes.count("SUCCEEDED")==9

def test_mock_does_not_claim_mismatch_when_order_matches(tmp_path):
    folder=tmp_path/"matching"
    folder.mkdir()
    (folder/"form.txt").write_text("2번 문항 지원동기\n3번 문항 개인 강점\n",encoding="utf-8")
    (folder/"review.txt").write_text("2번 문항 지원동기, 3번 문항 개인 강점, 문항 순서와 답변 일치\n",encoding="utf-8")
    (folder/"manifest.json").write_text('{"sources":[{"source_id":"form","path":"form.txt","provenance":"synthetic"},{"source_id":"review","path":"review.txt","provenance":"synthetic"}]}',encoding="utf-8")
    report=make(tmp_path,manifest=folder/"manifest.json").execute(GOAL)
    assert report.status == RunStatus.PARTIAL_COMPLETE
    assert len(report.cycles)==1
    assert report.cycles[0].analyst.findings==[]

def test_new_run_can_read_prior_research_but_not_gold_labels(tmp_path):
    class Spy(MockRunner):
        def __init__(self):self.lead_inputs=[]
        def run(self,role,payload,response_type):
            if role=="lead_plan":self.lead_inputs.append(payload)
            return super().run(role,payload,response_type)
    spy=Spy()
    flow=make(tmp_path,runner=spy)
    first=flow.execute(GOAL)
    flow.execute(GOAL)
    prior=spy.lead_inputs[1]["prior_research"]
    assert prior and prior[0]["run_id"]==first.run_id
    assert all("gold" not in str(item).lower() for item in prior)
