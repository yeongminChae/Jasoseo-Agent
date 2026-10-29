# PostgreSQL backend (SQLite remains supported)

This adds a second persistence backend for the **research run store** only.
The Evidence Registry still reads files through a manifest; this is **not** yet the
application-corpus or Batch Audit database. The original `Store` in `store.py`
continues to use SQLite and remains the default.

## Quick start (local Docker)

```bash
docker compose up -d postgres
python -m pip install -e '.[dev,postgres]'
export CAREER_AX_DATABASE_URL='postgresql://jasoseo:local_dev_only_change_me@localhost:5432/jasoseo'
python -m career_ax doctor
python -m career_ax run --storage postgres --runner mock --goal 'Analyze synthetic review omissions'
python -m pytest -q
```

On Windows PowerShell, replace `export ...` with:

```powershell
$env:CAREER_AX_DATABASE_URL='postgresql://jasoseo:local_dev_only_change_me@localhost:5432/jasoseo'
```

The first Docker initialization executes `database/migrations/V1__research_store.sql`.
**Existing Docker volumes do not re-run this script.** New schema changes require
migrations; do not drop real data to apply them. SQL file names follow Flyway
versioned migration conventions so the Spring backend can later adopt Flyway
as the single schema migration owner. Flyway itself is not yet wired into the CLI.

Default SQLite invocation is unchanged:

```bash
python -m career_ax run --runner mock --goal 'Analyze synthetic review omissions'
```

## Configuration and security

- `--storage sqlite` (default) uses `--db data/research.sqlite`.
- `--storage postgres` reads `CAREER_AX_DATABASE_URL` from the environment only.
- Do not check DSNs, credentials, private applicant documents, or run outputs into Git.
- The password in `compose.yaml` is **development-only**, bound to localhost.
- Production needs a different secret, restricted permissions, and TLS.
- Running live Codex requires separate `--runner codex --allow-external-model` consent.
- PostgreSQL integration tests run in CI against a disposable service database;
  locally they are skipped when `CAREER_AX_DATABASE_URL` is absent.

## Current limits

PostgreSQL is implemented but not validated against a live database in the
artifact authoring environment. GitHub Actions is configured to execute the
PostgreSQL integration tests on push. No claim of model quality or full
application-corpus migration is made.
