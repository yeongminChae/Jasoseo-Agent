# MVP: local Codex Review + human-adjudicated synthetic Eval

## Architecture and implementation boundary

START -> Analyst/Critic -> deterministic Python Verifier -> report.
Analyst and Critic make independent stateless Codex CLI calls. They are **not Codex subagents discussing with each other**. The verifier checks source ID, line, exact quote, duplicates, and possible conflicts; it does **not** prove semantic truth.

## 1. Local Codex CLI prerequisite (macOS; not nested in Codex Cloud)

In Codex Cloud, nested CLI failed with Read-only file system (os error 30). A local Mac may behave differently, but this has **not yet been verified**. Install from the official CLI distribution if needed (npm install -g @openai/codex@latest).

~~~bash
codex --version
codex login status
# If not logged in: codex login   (sign in with ChatGPT account)
git fetch origin codex/submission-review-mvp
git switch codex/submission-review-mvp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
~~~

Verify ChatGPT account authentication, not API-key authentication. Codex CLI via ChatGPT login is distinct from OpenAI API billing; usage limits still apply. Never copy secret auth files, tokens or private inputs into GitHub.

## 2. Live smoke: ONLY after explicit approval to send synthetic source text

Read the JSON first. Call only after user approves sending that exact synthetic input to the model.

~~~bash
python -m career_ax submission-review \
 --request fixtures/submission_review/missing_cause_validation/request.json \
 --runner codex --allow-external-model --timeout-seconds 180 \
 --output-dir runs/live/missing_cause_validation
~~~

For reproducibility, add --codex-model MODEL_ID when you have checked the local CLI model access; this is optional and does not establish which model actually ran. The configured value appears as requested_model in the saved report.\n\nIf it succeeds, review.json/review.md will be written. Record timestamp, commit hash, request input SHA256, CLI version, configured model, successful roles, exit/error details and redacted logs. The current implementation does NOT capture a verified actual model identifier or token usage in structured output. Do not guess these. The OpenAI API runner is an unused option; do not enable it without separate approval.

## 3. Three separate synthetic cases

| Directory | Expected defects | Evaluation focus |
| --- | --- | --- |
| missing_cause_validation | 2 | Cause missing; effect validation missing |
| complete_response | 0 | Normal answer; false-positive detection |
| unsupported_quantitative | 1 | Unsupported 50% improvement number |

Every case has request.json and gold.json. Pass **only request.json** to Analyst/Critic. Never include gold.json, human judgments, historical corrections, or previous model outputs in role inputs.

## 4. Offline human-adjudicated Eval: no model/network calls

Create blank judgments from a successful live run:

~~~bash
python -m career_ax.eval_submission prepare \
 --review runs/live/missing_cause_validation/review.json \
 --out runs/live/missing_cause_validation/adjudication.json
~~~

Manually annotate **every** Analyst/Critic finding in the generated JSON:

- match: valid Gold defect, set gold_defect_id to one existing Gold ID and write a concrete reason.
- additional_valid: valid but not preset in Gold; set gold_defect_id to null and explain.
- false_positive: invalid/unsubstantiated; set gold_defect_id to null and explain.

Example JSON judgment (replace the finding_id with a real returned ID):

~~~json
{"role":"analyst","finding_id":"analyst_1","verdict":"match","gold_defect_id":"GOLD_MISSING_CAUSE","reason":"The full answer does not identify the cause required by the question."}
~~~

Score the annotated run:

~~~bash
python -m career_ax.eval_submission score \
 --review runs/live/missing_cause_validation/review.json \
 --request fixtures/submission_review/missing_cause_validation/request.json \
 --gold fixtures/submission_review/missing_cause_validation/gold.json \
 --adjudication runs/live/missing_cause_validation/adjudication.json \
 --out runs/live/missing_cause_validation/eval.json
~~~

The scorer rejects Stub/Fake, failed or structurally invalid live runs, input-hash mismatch, Gold-ID mismatch and unjudged findings. It reports Gold TP/FN/recall, human-judged false positives and finding precision for Analyst, Critic and their union. Duplicate hits on one Gold item count once for recall. Human judgment is fallible; Gold may be incomplete. The Analyst-only comparator is a role baseline, not an independently optimized single-agent baseline.

Repeat for all three cases. Keep results local under runs/, excluded from Git. Do not claim general accuracy, career outcomes, or verified semantic fact-checking from these synthetic cases.

## 5. Submission evidence checklist

Show the commit hash, T0 input SHA256, CLI version, confirmed authentication *mode*, configured vs verified model identifier, execution outcome for each role, citation checks, original report, blinded Gold, human adjudication, per-case Eval, failures and limitations. Distinguish Codex-written code from changes personally understood and tested. Do not call a fake runner output a live-model result.
