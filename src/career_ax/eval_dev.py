"""Synthetic-only regression smoke suite; NOT a human-labeled model quality Eval."""
from __future__ import annotations
from pathlib import Path
import tempfile
from .evidence import Registry
from .models import RunStatus
from .runner import MockRunner
from .store import Store
from .workflow import Workflow

GOAL = "문항 순서와 답변의 검토 누락이 있는지 조사하고 필요시 다음 질문을 정해줘"
CASE_EXPECTATIONS = [
    ("synthetic_question_swap", "COMPLETE", 2),
    ("matching_case", "PARTIAL_COMPLETE", 1),
    ("missing_evidence", "BLOCKED", 0),
]

def execute_suite(fixtures_root: Path):
    results=[]
    with tempfile.TemporaryDirectory(prefix="career-ax-eval-") as td:
        for name, expected_status, expected_cycles in CASE_EXPECTATIONS:
            registry=Registry(fixtures_root/name/"manifest.json",scope="audit")
            store=Store(Path(td)/f"{name}.sqlite")
            report=Workflow(registry,store,MockRunner()).execute(GOAL)
            passed=(report.status.value==expected_status and len(report.cycles)==expected_cycles)
            if name=="synthetic_question_swap":
                passed=passed and bool(report.cycles[1].based_on_finding_ids) and "SYN_CORRECTION_POSTHOC" not in registry.lines
            results.append({"case":name,"expected_status":expected_status,"actual_status":report.status.value,
                           "expected_cycles":expected_cycles,"actual_cycles":len(report.cycles),"passed":passed,
                           "real_model_used":False})
    return {"suite":"SYNTHETIC_MOCK_SMOKE","not_a_model_quality_eval":True,
            "cases":results,"passed":sum(x["passed"] for x in results),"total":len(results),
            "note":"Synthetic case contract checks; do not report as GPT defect detection accuracy."}
