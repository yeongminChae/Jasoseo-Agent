# Application Improvement — automated V1 triage → V2 proposal → optional V2 review

> This reduces 37 manually inspected AI findings to **AI-proposed issue groups and one revised draft per question**. It is *not* verified semantic truth, an independently human-adjudicated Eval, or an automatic job application submission.

## Why this exists

The original 4-question review produced 37 Findings. Reviewing all 37 manually undermines the benefit of the system. The next pipeline now supports:

1. **Deterministic offline preflight:** verify that every existing V1 review belongs to the **exact** application input SHA256 and that Analyst/Critic both completed with structural evidence checks.
2. **One Codex editor call per question:** triage *every* Analyst/Critic Finding into `actionable`, `duplicate`, `normal`, `fact_check`, or `irrelevant`; group overlapping claims under the same `group_id`, rank priority and propose a 600-character-or-less revised draft.
3. **Deterministic checks:** validate that all original Finding IDs were classified exactly once; reject hallucinated/new IDs, oversize drafts; flag newly introduced numeric/date tokens. Preserve original V1 unmodified.
4. **Optional V2 recheck:** independent Analyst/Critic per revised question, with stored JSON/Markdown reports. This checks model-output structure and sources, **not actual improvement**.
5. **Human final gate:** show only unique issue groups, V2 drafts and explicit claims needing verification. Do **not** auto-approve or submit.

Two independent reviewers and one editor constitute three role-specific Codex calls per V2 question **before** recheck, not necessarily three different models. Each role has its own instructions; Editor sees the two reviewers' outputs. Recheck is a new independent pair, not editor self-adjudication.

## Commands in existing local repo

```bash
git switch codex/submission-review-mvp
git pull --ff-only
./.venv/bin/python -m pytest -q

# Step 1: Offline only, verify all 4 existing review.json files and input hashes.
./.venv/bin/python -m career_ax application-improve \
  --request private/imsec/application.json \
  --review-dir runs/private/imsec-v1 \
  --output-dir runs/private/imsec-improve-v1 \
  --v2-request private/imsec/application-v2.json \
  --dry-run

# Step 2: Only after explicitly approving further private-content model transmission:
# 4 editor calls, followed by an optional 8 Analyst/Critic recheck calls.
./.venv/bin/python -m career_ax application-improve \
  --request private/imsec/application.json \
  --review-dir runs/private/imsec-v1 \
  --output-dir runs/private/imsec-improve-v1 \
  --v2-request private/imsec/application-v2.json \
  --allow-external-model \
  --allow-private-input \
  --timeout-seconds 180 \
  --recheck

# Result: consolidated plan and revised 4-question draft (not a final/approved application).
open runs/private/imsec-improve-v1/improvement.md
open private/imsec/application-v2.json
open runs/private/imsec-improve-v1/recheck/summary.md
```

If Codex usage quota or runtime is tight, **omit** `--recheck` on the live command. You still get automated triage and V2 candidate; you can recheck after manual fact verification as a separate step (use a fresh run folder and updated V2 request). A completely automated full loop costs approximately 12 role invocations for four questions (up to 28 attempts if every Codex reviewer hits a retry ceiling; note Editor currently has no retry). These calls use the authenticated Codex CLI, not the paid OpenAI API backend; ChatGPT plan usage limits may still apply.

All new outputs are private, local files:

```text
private/imsec/application-v2.json                 # editable candidate, never written over V1
runs/private/imsec-improve-v1/
  improvement.json                               # all 37 role-qualified Finding classifications
  improvement.md                                 # grouped/prioritized issues + 4 revised drafts
  recheck/summary.json                           # only with --recheck
  recheck/summary.md
  recheck/q1/review.json, review.md
  ...
```

Neither `private/` nor `runs/` are tracked by Git. The command refuses to replace existing V2 paths or improvement output, and requires the input request, existing review output and new output to resolve below the appropriate ignored directories.

## What to inspect, and why

1. **Prioritized groups, not all 37 Findings.** Read high-priority group reasons and the resulting V2 text. AI groupings can miss synonyms or merge unrelated concerns.
2. **Source of claims.** Particularly iM증권 product changes and the 350-store POS project scale: a source quote confirms the original draft says the claim, **not that the claim is true**. Check published company releases or your work records; do not exaggerate per-person responsibility.
3. **Revised factual assertions.** The numeric-token guard flags new numbers, not fabricated qualitative impact, changing existing numbers, or reinterpretations. No automated fact-validation exists.
4. **Character limits.** The tool verifies Python string length ≤600 for each V2 draft. Compare with official recruiting form count, including possible newline or encoding rules.
5. **Before-and-after.** Recheck outputs can still contain duplicate/non-defect findings. Fewer Findings do not imply better essay quality. Do not report recall/precision without independent, blinded Gold or human judging.

Keep AI-proposed decisions and user-approved decisions distinct. The project reduces manual review effort **but does not eliminate your responsibility for factual accuracy, appropriateness, and submission**.

## What remains out of scope

No automatic applicant-source retrieval, full 65-case historical audit, RAG, official job application website submission, automatic fact provenance from employer records, ground-truth semantic validation or optimizer that guarantees a better essay. For 2026-11-08, prioritize a defensible revised iM Securities application instead of adding these features.
