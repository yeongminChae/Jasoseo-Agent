from pathlib import Path
from career_ax.eval_dev import execute_suite

def test_synthetic_smoke_contract():
    data=execute_suite(Path(__file__).resolve().parents[1]/"fixtures")
    assert data["passed"]==data["total"]==3
    assert data["not_a_model_quality_eval"]
