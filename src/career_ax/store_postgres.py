"""PostgreSQL persistence matching the existing SQLite Store contract.

Schema is owned by database/migrations; run the migration before connecting.
This module is optional so SQLite-only installs never require psycopg.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import CycleRecord, FinalReport, RunStatus


class PostgresStore:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL connection string is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('Install PostgreSQL support: pip install -e ".[postgres]"') from exc
        # Autocommit prevents read queries from holding idle transactions;
        # explicit transaction() below handles multi-statement atomic writes.
        self.connection = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)
        try:
            self.connection.execute("SELECT 1 FROM runs LIMIT 0")
        except Exception:
            self.connection.close()
            raise RuntimeError("PostgreSQL schema missing: apply database/migrations/V1__research_store.sql") from None

    def close(self):
        self.connection.close()

    def new(self, run_id: str, goal: str, runner: str, snapshot: str):
        with self.connection.transaction():
            self.connection.execute(
                "INSERT INTO runs(run_id,goal,runner,source_snapshot,status) VALUES(%s,%s,%s,%s,%s)",
                (run_id, goal, runner, snapshot, RunStatus.RUNNING.value),
            )

    def get(self, run_id: str) -> dict:
        row = self.connection.execute(
            "SELECT run_id,goal,runner,source_snapshot,status,calls_used,start_question,"
            "pending_question,pending_based_on,notes FROM runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def cycles(self, run_id: str) -> list[CycleRecord]:
        rows = self.connection.execute(
            "SELECT payload FROM cycles WHERE run_id=%s ORDER BY idx", (run_id,)
        ).fetchall()
        return [CycleRecord.model_validate_json(r["payload"]) for r in rows]

    def reserve_call(self, run_id: str, role: str, cycle: int, input_sha256: str, mode: str) -> int:
        # Both attempt record and accounting update succeed or fail together.
        with self.connection.transaction():
            result = self.connection.execute(
                "INSERT INTO agent_calls(run_id,role,cycle,input_sha256,output_sha256,mode,outcome) "
                "VALUES(%s,%s,%s,%s,NULL,%s,'STARTED') RETURNING seq",
                (run_id, role, cycle, input_sha256, mode),
            ).fetchone()
            self.connection.execute(
                "UPDATE runs SET calls_used=calls_used+1 WHERE run_id=%s", (run_id,)
            )
            return int(result["seq"])

    def complete_call(self, seq: int, outcome: str, output_sha256: str | None = None):
        with self.connection.transaction():
            self.connection.execute(
                "UPDATE agent_calls SET outcome=%s,output_sha256=%s WHERE seq=%s",
                (outcome, output_sha256, seq),
            )

    def set_plan(self, run_id: str, question: str):
        with self.connection.transaction():
            self.connection.execute("UPDATE runs SET start_question=%s WHERE run_id=%s", (question, run_id))

    def save_cycle(self, run_id: str, cycle: CycleRecord):
        with self.connection.transaction():
            self.connection.execute(
                "INSERT INTO cycles(run_id,idx,payload) VALUES(%s,%s,%s)",
                (run_id, cycle.index, cycle.model_dump_json()),
            )
            self.connection.execute(
                "UPDATE runs SET pending_question=%s,pending_based_on=%s WHERE run_id=%s",
                (cycle.next_question, json.dumps(cycle.decision.based_on_finding_ids), run_id),
            )

    def finish(self, run_id: str, status: RunStatus, note: str):
        # Lock existing row so concurrent note updates cannot overwrite each other.
        with self.connection.transaction():
            record = self.connection.execute("SELECT notes FROM runs WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
            if record is None:
                raise KeyError(run_id)
            notes = json.loads(record["notes"])
            if note and note not in notes:
                notes.append(note)
            self.connection.execute(
                "UPDATE runs SET status=%s,notes=%s WHERE run_id=%s",
                (status.value, json.dumps(notes, ensure_ascii=False), run_id),
            )

    def prior_verified_summary(self, snapshot: str, exclude_run_id: str, limit: int = 4) -> list[dict]:
        rows = self.connection.execute(
            "SELECT run_id,goal,status FROM runs "
            "WHERE source_snapshot=%s AND run_id<>%s AND status IN ('COMPLETE','PARTIAL_COMPLETE') "
            "ORDER BY created_seq DESC LIMIT %s",
            (snapshot, exclude_run_id, limit),
        ).fetchall()
        output = []
        for row in rows:
            observations = []
            for cycle in self.cycles(row["run_id"]):
                for finding in cycle.analyst.findings + cycle.critic.findings:
                    if finding.finding_id in cycle.accepted_finding_ids:
                        observations.append({"finding_id": finding.finding_id, "kind": finding.kind, "statement": finding.statement})
            output.append({
                "run_id": row["run_id"], "goal": row["goal"], "status": row["status"],
                "citation_gated_findings": observations[:12],
                "warning": "prior AI findings are not independent ground truth",
            })
        return output

    def report(self, run_id: str) -> FinalReport:
        row = self.get(run_id)
        return FinalReport(
            run_id=run_id, goal=row["goal"], status=RunStatus(row["status"]),
            runner=row["runner"], is_real_llm=row["runner"] == "codex",
            source_snapshot=row["source_snapshot"], cycles=self.cycles(run_id),
            calls_used=row["calls_used"], model_cost_usd=None, notes=json.loads(row["notes"]),
        )

    def export(self, run_id: str, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.report(run_id).model_dump_json(indent=2), encoding="utf-8")
