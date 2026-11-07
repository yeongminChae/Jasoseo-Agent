# Historical application finish (Codex CLI, local/private)

This command extends existing `career_ax` instead of creating a second project.

`application-history-finish` runs:
1. Historical auditor: apply historical failures and 12 evidence gates to the **current** draft, with exact-substring citations to the current text.
2. Editor: propose a complete question-matched revision; reject unsupported new numbers, >600-character answers, and duplicate/missing questions.
3. Independent A/B comparator: compare drafts in deterministic concealed order; keep original on TIE or INCONCLUSIVE. This is **model preference**, not Gold Eval or a reliable hiring score.
4. Fresh red-team: check selected final candidates against original JD and career boundaries without seeing the first three agents' judgments.

The command preserves each stage under a **new** `runs/` folder. It never overwrites input drafts, changes applicant data in Git, or submits an application. `GO` from the final model does not establish real-world fact verification.

## Prepare private inputs

Place the current submission candidate in `private/imsec/application-current.json` and the history evidence packet in `private/imsec/history-evidence.json`. Both must be under `private/` and excluded from Git. The history packet has the keys `history_gates`, `historical_examples`, and `career_boundaries`.

Do not commit the candidate or historical data: previous chat snapshots may include personal background and application drafts. Historical grades A/B/C are evidence provenance, not guaranteed original submitted answers.

## One experiment, no OpenAI API key

```bash
git switch codex/submission-review-mvp
git pull --ff-only

./.venv/bin/python -m pytest -q

./.venv/bin/python -m career_ax application-history-finish \
  --request private/imsec/application-current.json \
  --history private/imsec/history-evidence.json \
  --output-dir runs/private/imsec-history-20261107 \
  --dry-run

./.venv/bin/python -m career_ax application-history-finish \
  --request private/imsec/application-current.json \
  --history private/imsec/history-evidence.json \
  --output-dir runs/private/imsec-history-20261107 \
  --allow-external-model --allow-private-input --timeout-seconds 240

open runs/private/imsec-history-20261107/summary.md
```

The command explicitly requires permission flags before any model call. It uses the existing authenticated Codex CLI via four `codex exec` subprocesses, **not** the paid OpenAI API runner. Start it from a normal terminal, not from inside an already-running Codex agent.

Outputs:
- `01_historical_audit.json`
- `02_history_revision.json`
- `03_blind_comparison.json`
- `04_final_redteam.json`
- `candidate.json` (four chosen drafts)
- `summary.json`, `summary.md`

If one model call fails, the run is incomplete; earlier successful stage files are preserved. Use a different output directory for a retry; do not report success from partial output.

The `candidate.json` is a draft for applicant review. Company motivation must be genuine; employer performance and personal role facts require real records or explicit user confirmation. The official recruiting form may count characters differently and must be checked before submission.
