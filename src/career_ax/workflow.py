"""Bounded autonomous research loop; AI proposes, Python approves and persists."""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from typing import Type
from pydantic import BaseModel
from .evidence import Registry
from .models import (
    AgentCall, Assessment, Collection, CycleRecord, Decision, NextAction, Plan,
    RunStatus,
)
from .runner import AgentRunner, AgentRuntimeError
from .store import Store
from .verifier import verify, followup_is_linked

class BudgetReached(RuntimeError): pass

class Workflow:
    def __init__(self, registry: Registry, store: Store, runner: AgentRunner,
                 *, max_cycles: int = 3, max_calls: int = 20, max_retries: int = 1):
        if not 1 <= max_cycles <= 3:
            raise ValueError("Pilot max_cycles must be 1 to 3")
        if not 1 <= max_calls <= 50:
            raise ValueError("Pilot max_calls must be 1 to 50")
        if not 0 <= max_retries <= 2:
            raise ValueError("Pilot max_retries must be 0 to 2")
        self.registry, self.store, self.runner = registry, store, runner
        self.max_cycles, self.max_calls, self.max_retries = max_cycles, max_calls, max_retries

    def invoke(self, run_id: str, role: str, cycle: int, payload: dict, typ: Type[BaseModel]):
        def digest(x):
            return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        for attempt in range(self.max_retries+1):
            if self.store.get(run_id)["calls_used"] >= self.max_calls:
                raise BudgetReached("max_calls reached")
            seq=self.store.reserve_call(run_id,role,cycle,digest(payload),self.runner.mode)
            try:
                result = self.runner.run(role, payload, typ)
                self.store.complete_call(seq,"SUCCEEDED",digest(result.model_dump(mode="json")))
                return result
            except (AgentRuntimeError, TimeoutError) as exc:
                self.store.complete_call(seq,"FAILED")
                if attempt == self.max_retries:
                    raise
            except Exception:
                self.store.complete_call(seq,"FAILED")
                raise # Deterministic/programming errors must not be retried.
        raise RuntimeError("Unreachable")

    def execute(self, goal: str | None = None, *, run_id: str | None = None):
        if run_id is None:
            if not goal or not goal.strip():
                raise ValueError("goal is required for a new run")
            run_id = uuid.uuid4().hex[:16]
            self.store.new(run_id,goal,self.runner.mode,self.registry.snapshot)
        else:
            prev = self.store.get(run_id)
            if prev["source_snapshot"] != self.registry.snapshot or prev["runner"] != self.runner.mode:
                raise ValueError("Resume refused: source snapshot or runner changed. Start a new run.")
            if prev["status"] == RunStatus.COMPLETE.value:
                raise ValueError("Completed run is immutable; start a new run")
            if prev["status"] not in {RunStatus.RUNNING.value,RunStatus.BUDGET_EXHAUSTED.value}:
                raise ValueError("Cannot resume terminal run")
            goal = prev["goal"]
        try:
            self._cycles(run_id,goal)
        except BudgetReached:
            self.store.finish(run_id, RunStatus.BUDGET_EXHAUSTED, "모델 호출 상한 도달; 횟수를 늘려 재개 가능. 비용(USD)은 계측하지 않음")
        except Exception as exc:
            self.store.finish(run_id, RunStatus.FAILED, f"{type(exc).__name__}: {exc}")
            raise
        return self.store.report(run_id)

    def _cycles(self, run_id: str, goal: str):
        old = self.store.cycles(run_id)
        row = self.store.get(run_id)
        if old and old[-1].decision.action != NextAction.CONTINUE:
            raise ValueError("No pending research question from completed cycle")
        if len(old) >= self.max_cycles:
            self.store.finish(run_id,RunStatus.BUDGET_EXHAUSTED,"사이클 상한 도달")
            return
        if old:
            question, based_on = old[-1].next_question, old[-1].decision.based_on_finding_ids
        else:
            if row["start_question"]:
                question = row["start_question"]
            else:
                plan = self.invoke(run_id,"lead_plan",0,{"goal":goal,"available_sources":self.registry.inventory(),
                    "prior_research":self.store.prior_verified_summary(self.registry.snapshot,run_id)},Plan)
                question = plan.question
                self.store.set_plan(run_id,question)
            based_on = []
        for idx in range(len(old)+1,self.max_cycles+1):
            if not question:
                self.store.finish(run_id,RunStatus.BLOCKED,"연구 질문이 없음")
                return
            # Reject repeated question even when the Lead says CONTINUE.
            if question in [c.question for c in old]:
                self.store.finish(run_id,RunStatus.PARTIAL_COMPLETE,"중복 질문 방지 게이트")
                return
            candidates = self.registry.search(question)
            packet = self.invoke(run_id,"collector",idx,{"cycle":idx,"question":question,
                                      "candidates":[c.model_dump() for c in candidates]},Collection)
            # Guard against fabricated collector citations BEFORE passing them to workers.
            if not packet.evidence or any(not self.registry.verify_ref(ref) for ref in packet.evidence):
                self.store.finish(run_id,RunStatus.BLOCKED,"원본 근거를 검색하지 못했거나 Collector 인용 검증 실패")
                return
            # No analyst output in critic payload and vice versa: T0 input isolation.
            worker_payload = {"cycle":idx,"question":question,"packet":packet.model_dump(),"rubric":"문서 관찰과 실제 채용 원인 추측을 구분. 인용 원문 필수."}
            analyst = self.invoke(run_id,"analyst",idx,worker_payload,Assessment)
            critic = self.invoke(run_id,"critic",idx,worker_payload,Assessment)
            verdict = verify(self.registry,packet,analyst,critic)
            valid_statements=[f.statement for f in analyst.findings + critic.findings if f.finding_id in verdict.valid_finding_ids]
            if not verdict.passed:
                # Invalid citations are reported but not sent to Lead as accepted facts.
                decision = Decision(action=NextAction.BLOCKED,reason="인용·구조 검증 실패; AI 결론을 사실로 저장하지 않음")
            elif not verdict.valid_finding_ids:
                decision = Decision(action=NextAction.REQUEST_EVIDENCE,reason="검증 가능한 발견 사항 부재")
            else:
                decision = self.invoke(run_id,"lead_next",idx,{"cycle":idx,"goal":goal,"question":question,
                                         "valid_finding_ids":verdict.valid_finding_ids,
                                         "verified_statements":valid_statements,
                                         "remaining_cycles":self.max_cycles-idx},Decision)
                if decision.action == NextAction.CONTINUE:
                    if not set(decision.based_on_finding_ids).issubset(verdict.valid_finding_ids):
                        decision = Decision(action=NextAction.HUMAN_REVIEW, reason="후속 질문의 근거 ID가 실제 검증 결과와 불일치")
                    elif not followup_is_linked(decision.next_question or "",valid_statements):
                        decision = Decision(action=NextAction.HUMAN_REVIEW, reason="후속 질문이 이전 발견과 연결된다는 최소 문자열 근거 없음")
                    elif decision.next_question == question:
                        decision = Decision(action=NextAction.BLOCKED, reason="동일한 질문을 무의미하게 반복함")
            cycle = CycleRecord(index=idx,question=question,based_on_finding_ids=based_on,
                                collection=packet,analyst=analyst,critic=critic,verification=verdict,
                                decision=decision,next_question=decision.next_question,
                                accepted_finding_ids=verdict.valid_finding_ids if verdict.passed else [])
            self.store.save_cycle(run_id,cycle)
            old.append(cycle)
            if decision.action == NextAction.CONTINUE:
                question,based_on=decision.next_question,decision.based_on_finding_ids
                if idx == self.max_cycles:
                    self.store.finish(run_id,RunStatus.BUDGET_EXHAUSTED,"연구 사이클 상한 도달; 질문을 보존하고 중지")
                    return
                continue
            if decision.action == NextAction.COMPLETE:
                status = RunStatus.COMPLETE if verdict.source_level != "SECONDARY_ONLY" else RunStatus.PARTIAL_COMPLETE
            elif decision.action == NextAction.HUMAN_REVIEW:
                status = RunStatus.HUMAN_REVIEW
            elif decision.action == NextAction.REQUEST_EVIDENCE:
                status = RunStatus.PARTIAL_COMPLETE
            else:
                status = RunStatus.BLOCKED
            note = ("합성(Mock) 결과이며 새로운 LLM 추론/성능 증명이 아님" if self.runner.mode=="mock" else
                    "실제 Codex 실행; 원문 인용 검증은 의미·인과관계의 진실을 보증하지 않음")
            self.store.finish(run_id,status,note)
            return
