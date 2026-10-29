# AX Pilot verification record (2026-10-27)

This is an **implementation run record**, not an independently verified AI quality score.

## Files/environment reviewed
- `Career_Intelligence_Agent_Integrated_Plan_v0.3.md`: design; no existing Career AX executable was present in the working folder.
- `career_intelligence_design_v0_1.md`: prior technical design.
- In connected user file library, `Career_Essay_Phase2_Review_Audit.md`, `Career_Essay_Rubric_v1.0.md` and `Phase3_Results_and_Limitations.md` were located and reviewed. These **retrospective** records are not to be substituted for the original T0 PDF/PNG input.
- Phase 3's existing report distinguishes original-source restoration and historical GPT-output evaluation from a *new inference baseline*. The new baseline was not executed in that work.
- Private research files were inspected separately and **not included in this project archive**.
- 2026-10-27 sandbox: Python 3.13.5, Pydantic 2.x, pytest present, `codex` executable absent.

## Executed checks

1. `python -m pip install -e . --no-deps --no-build-isolation` — successful.
2. `pytest -q` — **19 passed** after corrections.
3. `career-ax doctor` — executed; Codex CLI unavailable.
4. `career-ax eval-dev` — three synthetic tests passed (swapped order, correctly ordered, no evidence).
5. `career-ax run --runner mock ...` — successful with 2 linked research cycles, 9 role calls, citation validation PASS.
6. `career-ax inventory --scope replay_t0` — excluded the simulated historic GPT T0 response and later corrections, leaving only form input.
7. Private manifest containing only Phase 2, Phase 3 and Rubric retrospective documentation shows 0 sources in T0 scope and 3 secondary sources in research scope.

## Questions in the successful mock run

- Cycle 1: `과거 검토에서 문항 순서와 답변 대응을 원문과 대조했는가?`
- Cycle 2: `문항 순서 대조 누락을 예방하려면 어떤 질문-답변 번호 확인 절차가 필요한가?`

Cycle 2 names a verified Cycle 1 finding ID (`c1_analyst`). 'Verified' refers to line/quote consistency only, not external ground truth. The context for the second question is derived from the first cycle's model-shaped Mock outputs.

## Not done / must not be claimed

- **NO new model inference was performed**, and no current GPT defect detection score exists.
- Original PDF/PNG replays of R03/R04 are not performed by this pilot.
- Golden Dataset v0.2 was located but its original cases are **not imported into the mock suite**.
- USD/token metering not implemented; Python limits number of *attempted* invocations.
- Raw documents with personal information were excluded from deliverables.
- Reconciliation of 63 vs 65 application records is not done.

## Next meaningful experiment

On the user's authenticated private PC, add minimally redacted R03/R04 point-in-time original evidence as `t0_input`; keep GPT history output separate as `t0_output` and post-event corrections as `posthoc`. Run `inventory --scope replay_t0` before any LLM baseline. Freeze T0 output before exposing T1. Execute a real Codex baseline only after explicit personal data transfer approval. Evaluate error detection against independently adjudicated labels; do not merge historical GPT F labels with new T0 hit/miss scores.
