-- Research execution store. Applies to fresh PostgreSQL instances and Flyway.
-- Application evidence, audit corpus, and eval labels are future migrations.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_seq BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    goal TEXT NOT NULL,
    runner TEXT NOT NULL,
    source_snapshot TEXT NOT NULL,
    status TEXT NOT NULL,
    calls_used INTEGER NOT NULL DEFAULT 0 CHECK (calls_used >= 0),
    start_question TEXT,
    pending_question TEXT,
    pending_based_on TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS cycles (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    idx INTEGER NOT NULL CHECK (idx > 0),
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, idx)
);

CREATE TABLE IF NOT EXISTS agent_calls (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    role TEXT NOT NULL,
    cycle INTEGER NOT NULL CHECK (cycle >= 0),
    input_sha256 TEXT NOT NULL,
    output_sha256 TEXT,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_calls_run ON agent_calls(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_runs_snapshot ON runs(source_snapshot, created_seq DESC);
