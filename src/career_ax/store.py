"""Small durable SQLite event store: completed cycles commit atomically."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .models import AgentCall, CycleRecord, FinalReport, RunStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, goal TEXT NOT NULL, runner TEXT NOT NULL, source_snapshot TEXT NOT NULL,
 status TEXT NOT NULL, calls_used INTEGER NOT NULL DEFAULT 0, start_question TEXT, pending_question TEXT,
 pending_based_on TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS cycles (
 run_id TEXT NOT NULL REFERENCES runs(run_id), idx INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS agent_calls (
 run_id TEXT NOT NULL, seq INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
 cycle INTEGER NOT NULL, input_sha256 TEXT NOT NULL, output_sha256 TEXT, mode TEXT NOT NULL, outcome TEXT NOT NULL
);
"""

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)

    def new(self, run_id: str, goal: str, runner: str, snapshot: str):
        with self.connection:
            self.connection.execute("INSERT INTO runs(run_id,goal,runner,source_snapshot,status) VALUES(?,?,?,?,?)",
                                    (run_id, goal, runner, snapshot, RunStatus.RUNNING.value))

    def get(self, run_id: str) -> dict:
        cur = self.connection.execute("SELECT run_id,goal,runner,source_snapshot,status,calls_used,start_question,pending_question,pending_based_on,notes FROM runs WHERE run_id=?", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(zip([x[0] for x in cur.description], row))

    def cycles(self, run_id: str) -> list[CycleRecord]:
        cur = self.connection.execute("SELECT payload FROM cycles WHERE run_id=? ORDER BY idx", (run_id,))
        return [CycleRecord.model_validate_json(x[0]) for x in cur.fetchall()]

    def reserve_call(self, run_id: str, role: str, cycle: int, input_sha256: str, mode: str) -> int:
        # Charge the attempt before calling an external model, including failed attempts.
        with self.connection:
            cur = self.connection.execute("INSERT INTO agent_calls(run_id,role,cycle,input_sha256,output_sha256,mode,outcome) VALUES(?,?,?,?,?,?,?)",
                                          (run_id,role,cycle,input_sha256,None,mode,"STARTED"))
            self.connection.execute("UPDATE runs SET calls_used=calls_used+1 WHERE run_id=?",(run_id,))
            return int(cur.lastrowid)

    def complete_call(self, seq: int, outcome: str, output_sha256: str | None = None):
        with self.connection:
            self.connection.execute("UPDATE agent_calls SET outcome=?,output_sha256=? WHERE seq=?",
                                    (outcome,output_sha256,seq))

    def set_plan(self, run_id: str, question: str):
        with self.connection:
            self.connection.execute("UPDATE runs SET start_question=? WHERE run_id=?", (question,run_id))

    def save_cycle(self, run_id: str, cycle: CycleRecord):
        with self.connection:
            self.connection.execute("INSERT INTO cycles(run_id,idx,payload) VALUES(?,?,?)",
                                    (run_id,cycle.index,cycle.model_dump_json()))
            self.connection.execute("UPDATE runs SET pending_question=?,pending_based_on=? WHERE run_id=?",
                                    (cycle.next_question,json.dumps(cycle.decision.based_on_finding_ids),run_id))

    def finish(self, run_id: str, status: RunStatus, note: str):
        existing = json.loads(self.get(run_id)["notes"])
        if note and note not in existing:
            existing.append(note)
        with self.connection:
            self.connection.execute("UPDATE runs SET status=?,notes=? WHERE run_id=?",(status.value,json.dumps(existing,ensure_ascii=False),run_id))

    def prior_verified_summary(self, snapshot: str, exclude_run_id: str, limit: int = 4) -> list[dict]:
        """Only expose citation-gated observations from identical source bytes, never gold labels."""
        cur=self.connection.execute("""SELECT run_id,goal,status FROM runs
            WHERE source_snapshot=? AND run_id<>? AND status IN ('COMPLETE','PARTIAL_COMPLETE')
            ORDER BY rowid DESC LIMIT ?""",(snapshot,exclude_run_id,limit))
        out=[]
        for prior_run_id,goal,status in cur.fetchall():
            observations=[]
            for cycle in self.cycles(prior_run_id):
                for finding in cycle.analyst.findings+cycle.critic.findings:
                    if finding.finding_id in cycle.accepted_finding_ids:
                        observations.append({"finding_id":finding.finding_id,"kind":finding.kind,"statement":finding.statement})
            out.append({"run_id":prior_run_id,"goal":goal,"status":status,"citation_gated_findings":observations[:12],
                        "warning":"prior AI findings are not independent ground truth"})
        return out

    def report(self, run_id: str) -> FinalReport:
        row = self.get(run_id)
        return FinalReport(run_id=run_id,goal=row["goal"],status=RunStatus(row["status"]),
                           runner=row["runner"],is_real_llm=row["runner"]=="codex",
                           source_snapshot=row["source_snapshot"],cycles=self.cycles(run_id),
                           calls_used=row["calls_used"],model_cost_usd=None,notes=json.loads(row["notes"]))

    def export(self, run_id: str, dest: Path):
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(self.report(run_id).model_dump_json(indent=2),encoding="utf-8")
