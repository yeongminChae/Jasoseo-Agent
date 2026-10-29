"""Real PostgreSQL integration tests (opt in by setting CAREER_AX_DATABASE_URL)."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from career_ax.evidence import Registry
from career_ax.models import RunStatus
from career_ax.runner import MockRunner
from career_ax.store_postgres import PostgresStore
from career_ax.workflow import Workflow

DSN = os.environ.get("CAREER_AX_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="PostgreSQL DSN not configured")
DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_question_swap" / "manifest.json"
GOAL = "가상 자기소개서 문항 순서 누락을 분석하고 후속 검증을 수행하라"


def make_store():
    store = PostgresStore(DSN)
    return store


def test_postgres_two_cycles_and_call_journal(tmp_path):
    store = make_store()
    try:
        report = Workflow(Registry(DEMO), store, MockRunner()).execute(GOAL)
        assert report.status == RunStatus.COMPLETE
        assert len(report.cycles) == 2
        assert report.cycles[1].based_on_finding_ids
        assert report.calls_used == 9
        rows = store.connection.execute(
            "SELECT role,outcome FROM agent_calls WHERE run_id=%s ORDER BY seq", (report.run_id,)
        ).fetchall()
        assert len(rows) == 9 and all(r["outcome"] == "SUCCEEDED" for r in rows)
        path = tmp_path / "export.json"
        store.export(report.run_id, path)
        assert report.run_id in path.read_text(encoding="utf-8")
    finally:
        store.close()


def test_postgres_resume_and_research_reuse():
    store = make_store()
    try:
        flow = Workflow(Registry(DEMO), store, MockRunner(), max_calls=5)
        first = flow.execute(GOAL)
        assert first.status == RunStatus.BUDGET_EXHAUSTED
        assert len(first.cycles) == 1
        continued = Workflow(Registry(DEMO), store, MockRunner(), max_calls=20).execute(run_id=first.run_id)
        assert continued.status == RunStatus.COMPLETE
        assert continued.calls_used == 9
        assert len(continued.cycles) == 2
        summary = store.prior_verified_summary(Registry(DEMO).snapshot, exclude_run_id="not-existing")
        assert any(s["run_id"] == first.run_id for s in summary)
    finally:
        store.close()


def test_postgres_reservation_is_atomic_and_failures_counted():
    from career_ax.runner import AgentRuntimeError

    class Transient(MockRunner):
        first = True
        def run(self, role, payload, response_type):
            if role == "collector" and self.first:
                self.first = False
                raise AgentRuntimeError("simulated transient failure")
            return super().run(role, payload, response_type)

    store = make_store()
    try:
        report = Workflow(Registry(DEMO), store, Transient()).execute(GOAL)
        assert report.status == RunStatus.COMPLETE
        assert report.calls_used == 10
        rows = store.connection.execute(
            "SELECT outcome FROM agent_calls WHERE run_id=%s", (report.run_id,)
        ).fetchall()
        assert sum(r["outcome"] == "FAILED" for r in rows) == 1
    finally:
        store.close()


def test_postgres_requires_dsn():
    with pytest.raises(ValueError, match="required"):
        PostgresStore("")
