# Submission Review MVP — Live Codex & synthetic Eval evidence (2026-11-04)

> Evidence level: reports and scores were **run locally and shared by the user**. The files under `runs/live/` were not uploaded to GitHub and were not independently re-executed by the author of this report. Findings were annotated from an **AI-proposed adjudication**; the saved Case 03 note explicitly says **human review and approval pending**. Do not describe these as completed independent human-labeled Evals.

## Objective and architecture

Python 3.12 / LangGraph START -> independently executed Analyst and Critic Codex CLI calls -> deterministic Python Verifier -> `review.json`/`review.md` -> offline Gold Eval after inference. Analyst and Critic have role-specific instructions and separate model invocations, not a conversational subagent team. Verifier validates evidence IDs, line numbers and exact quoted substrings; it does **not** establish factual or causal truth. Gold was kept in a separate `gold.json` and was not part of submitted `request.json`.

Repository: [Jasoseo-Agent](https://github.com/yeongminChae/Jasoseo-Agent), work on [Draft PR #1](https://github.com/yeongminChae/Jasoseo-Agent/pull/1), branch `codex/submission-review-mvp`. The feature branch is not merged into main.

## Execution environment and evidence

- User-reported machine: macOS, Python 3.12.3 virtual environment, Codex CLI 0.164.0, `codex login status` = Logged in using ChatGPT.
- OpenAI API backend was not used. The run was authorized for **synthetic question/submission data only**.
- `requested_model` = null in all supplied reports. The **actual model identifier**, prompt/version fingerprints, per-role raw CLI transcripts, retry counts, tokens and dollar costs were **not captured**. Codex may be subject to plan usage quotas; do not claim zero resource consumption or a measured model bill.
- Initial attempts on the local machine reached the Codex service but failed with HTTP 400 `invalid_json_schema`: in strict Structured Outputs, all object property keys must be in `required`. Earlier Codex Cloud nested execution had failed differently (`Read-only file system (os error 30)`).
- Fix: `runner.py` recursively normalizes the Pydantic JSON Schema (`required` lists all `properties`, `additionalProperties=false`, removes defaults), while retaining Python-side Pydantic validation. Regression tests added. The fix was committed in `c35ac98` and `f80090e`.
- After that fix, the user reported `status=COMPLETE`, `real_llm_executed=true`, citation structural checks passed, failures `{}` and exit code 0 for Case 01; Case 02 and Case 03 successful real runs are confirmed by their reported output and the successful offline scoring gate.
- The historical GitHub Actions CI at commit `9c3c711` completed successfully with **82 tests** on the branch, using Mock/Stub and fake subprocess where applicable. This CI does **not** invoke a live model.

## Synthetic dataset, SHA256 and scored output

| Case | Request id | Input SHA256 | Gold defects | Analyst Gold | Critic Gold | Combined Gold |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 01: missing cause/validation | `synthetic-missing-cause-validation-001` | `ffa26471d589767c6158407a4a9c0a80c9359128e5a4d667fcd6370d0d4df274` | 2 | 2/2 | 2/2 | 2/2 |
| 02: complete response | `synthetic-complete-response-001` | `afb1f17b44cb367d32a47c79cc827071c1d98bfd52b8d2bd0911cf58a67e71c8` | 0 | N/A | N/A | N/A |
| 03: unsupported 50% claim | `synthetic-unsupported-quantitative-001` | `9a5cddbe7d0424b9de28d489675fcbab2953a8f2adff01e542373e5a3519a5b8` | 1 | 1/1 | 1/1 | 1/1 |

### Finding-level results (AI-proposed judgments, scored locally)

| Case | All role findings | Matched defect findings | False-positive defect claims | Valid non-defect findings | Out of scope | Defect-finding precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 01 | 7 | 6 | 1 | 0 | 0 | 6/7 = 85.7% |
| 02 | 6 | 0 | 0 | 5 | 1 | N/A: no defect finding |
| 03 | 6 | 3 | 0 | 2 | 1 | 3/3 = 100% |
| Total | **19** | **9** | **1** | **7** | **2** | **9/10 = 90%** |

Totals count *Finding occurrences*, not 19 unique defects. The 9 matched defect findings refer to only **3 unique Gold defects**. They must not be counted as 9 distinct correct defects. Across the two defective cases, the descriptive Gold recall is **3/3** for Analyst alone, Critic alone and their union; the observed incremental Gold coverage of adding Critic to Analyst is **0**. This is a tiny synthetic dataset, not a benchmark accuracy, hiring success-rate or evidence of model-quality improvement.

### Disagreements and limitations

- Case 01 Analyst `analyst-001` was adjudicated false-positive: it mentioned missing responsibility range and duration which the question did not require. Other six Findings matched one of the two Gold omissions.
- Case 02 both reviewers correctly reported coverage of the requested components. They also raised qualified cautions about the rate denominator and causal interpretation. The Critic's real-vs-fictional concern was judged **out of scope** in this intentionally synthetic scenario, not classified as a hallucinated fact.
- Case 03 both reviewers identified an unsupported 50% improvement claim without asserting it was false. The Analyst's `analyst-003` mixed a useful metric-definition concern with an out-of-scope fictional-case concern; current scorer permits only **one verdict per Finding**.
- The code's `conservative_exact_match` integration reported semantically duplicate findings as distinct. The combined report improves traceability but **does not demonstrate better defect detection** here.
- Independent AI roles can share a model and correlated failure modes. Correct citation strings do not prove the attached interpretation. No semantic fact validator, independent human-blinded judging, prompt-ablation baseline, or repeated-seed robustness study was performed.
- `evaluation_type` in local scorer output is `human_adjudicated_synthetic_case` by schema, **but the supplied Case 03 `notes` explicitly says `AI-proposed judgments; human review and approval pending`**. The label alone cannot be used as proof of human review. Every adjudication must be reviewed and signed off manually before claiming human-grounded Eval.

## Exact local files (not included in GitHub)

- `runs/live/case01/{review.json,review.md,adjudication.json,eval.json}`
- `runs/live/case02/{review.json,review.md,adjudication.json,eval.json}`
- `runs/live/case03/{review.json,review.md,adjudication.json,eval.json}`

These live artifacts should be retained locally with timestamps and source commit SHA. No local files have been copied to this report. Public repository must not include personal essays, auth files, actual applicant details or hidden credentials.

## Reproduction commands (local macOS)

~~~bash
git switch codex/submission-review-mvp
git pull --ff-only
codex --version
codex login status
./.venv/bin/python -m pytest -q

# Model-transmitting step: requires explicit authorization; use synthetic request.json only.
./.venv/bin/python -m career_ax submission-review \
  --request fixtures/submission_review/unsupported_quantitative/request.json \
  --runner codex --allow-external-model --timeout-seconds 180 \
  --output-dir runs/live/case03

# Offline: no model calls. Inspect/approve all individual verdicts before scoring.
./.venv/bin/python -m career_ax.eval_submission prepare \
  --review runs/live/case03/review.json \
  --out runs/live/case03/adjudication.json
./.venv/bin/python -m career_ax.eval_submission score \
  --review runs/live/case03/review.json \
  --request fixtures/submission_review/unsupported_quantitative/request.json \
  --gold fixtures/submission_review/unsupported_quantitative/gold.json \
  --adjudication runs/live/case03/adjudication.json \
  --out runs/live/case03/eval.json
~~~

The first command for a new run will overwrite existing `case03` reports. For an additional trial, use a **new** output directory such as `runs/live/case03-repeat01` and record its timestamp and model configuration.

## 2026-11-06 MVP finish checklist

- [x] LangGraph fan-out/barrier, independent LLM roles, deterministic verifier, JSON/Markdown output
- [x] Codex account login confirmed and live Analyst/Critic outputs for three synthetic cases (user-run, user-reported)
- [x] Three separate Gold fixtures and offline scoring logic
- [x] Code CI tests (GitHub Actions, no live model)
- [x] Observed result table, first schema failure + fix documented
- [ ] User independently reviews/approves every AI-proposed adjudication; record reviewer, date and any changed labels
- [ ] Preserve **redacted local raw live run files** and record Git source commit, Codex CLI version, run time, configured/verified model identifiers if accessible
- [ ] Update final resume/essay description to claim only measured facts; do not claim improved detection rate, universal reliability, semantic verification or hiring outcomes

## Resume/interview language (evidence-limited)

Designed a Python/LangGraph workflow that independently runs Analyst and Critic via authenticated Codex CLI, then checks citation structure in a deterministic verifier and produces reproducible JSON/Markdown outputs. Built an isolated three-case synthetic Gold dataset and an offline adjudication-based Eval, measured duplicate findings and an observed 0 additional Gold defects from the second role, and corrected a real strict JSON Schema compatibility failure. Synthetic tests show the mechanics and reveal limits; they do not establish general model accuracy or hiring benefits. When discussing authorship, distinguish AI-generated patches from work personally reviewed and validated.
