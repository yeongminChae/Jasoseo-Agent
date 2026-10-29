# Submission Review MVP skeleton

이 Sprint는 LangGraph 실행 골격과 입력/출력 계약을 제공합니다. 실제 자기소개서 검토,
역할별 프롬프트, 결과 통합/충돌 정책, 재시도/중단 정책은 구현하지 않았습니다.
합성 Stub 실행 성공을 실제 모델 성능이나 의미 검증 완료로 해석하면 안 됩니다.

## 실행

Python 3.11 이상:

```sh
python -m pip install -e '.[dev,postgres]'
python -m career_ax submission-review \
  --request fixtures/submission_review/request.json \
  --runner stub --output-dir runs/submission-review
python -m pytest -ra
```

`--runner`의 기본값은 `unimplemented`이며 명시적으로 실패합니다. Stub을 선택해야
합성 데이터로 실행됩니다. JSON/Markdown 결과는 `review.json`, `review.md`에 저장되고,
같은 출력 디렉터리에 다시 실행하면 덮어씁니다. 결과의 `STRUCTURAL_CHECKS_ONLY`,
`semantic_review_complete=false`, `integration_policy=TODO`는 의도된 미완료 표시입니다.
실패 시 성공 보고서를 새로 만들지 않으며 예외를 전파합니다. CLI는 종료 코드 2를 반환합니다.
기존 출력 파일은 실패해도 지우지 않으므로 실행별 디렉터리를 권장합니다.

## 실행 흐름 및 독립성

```text
                     ┌ Analyst → analyst: Assessment ┐
START (T0 request) ───┤                               ├→ Verifier → END
                     └ Critic  → critic: Assessment  ┘
```

두 노드는 START에서 같은 superstep에 실행됩니다. 두 분기가 끝나야 Verifier를 한 번
실행합니다. `ReviewInput`에는 `request`만 있고, 노드별 `input_schema`가 상대 결과를
숨깁니다. 각 노드는 복사·재검증한 불변 `ReviewRequest`만 핵심 함수에 넘기며,
원본과 별개인 `analyst`, `critic` 채널만 기록합니다. 공유 메시지 채널은 없습니다.
이 방식은 [LangGraph multiple schemas 및 병렬 실행 모델](https://docs.langchain.com/oss/python/langgraph/graph-api)을 따릅니다.

`AgentRunner`, `Assessment`, `Finding`, `EvidenceRef`, `Verification`을 재사용합니다.
새로운 LLM 실행기를 만들지 않았습니다. `SubmissionStubRunner`는 합성 인용 한 건을
반환하는 테스트용 구현이며 내용 분석을 하지 않습니다.
Verifier는 원본의 source ID/행/부분 인용과 finding ID 중복만 검사합니다. 빈 findings도
구조 검사를 통과할 수 있습니다. 인용 존재는 주장 진실성, 합의, 채용 결과를 증명하지 않습니다.
두 원시 Assessment를 그대로 보존하며 findings를 통합하거나 채택하지 않습니다.

## T0 입력 경계

`ReviewRequest`는 자체 완결된 원본 스냅샷입니다. 모든 source는 명시적인
`phase=t0_input`, purpose(`question`, `submission`, `rubric`, `reference`)를 요구하며
알 수 없는 필드를 거부합니다. 질문/지원서 원문이 필수이고 중복 source ID를 거부합니다.
posthoc, t0_output, Golden Dataset, 이전 AI 분석 필드는 허용되지 않습니다.
기존 연구 Registry나 저장소의 prior research를 자동 조회하거나 입력에 합치지 않습니다.

이 스키마는 내용의 출처를 자동 판별하지 않습니다. 사후 정정이나 정답을 원문이라고
거짓 표시하거나 원문 text 안에 섞으면 탐지할 수 없습니다. 입력 준비 담당자는 최초 검토
시점의 자료만 선별하고, 평가 정답/이전 AI 결론은 별도 평가 경로에 보관해야 합니다.
실제 개인정보/자기소개서는 `private/`에, 실행 산출물은 `runs/`에 보관하고 Git에 추가하지 마세요.
커밋된 fixture는 전부 가상의 문항·답변이며 정답/성능 점수를 포함하지 않습니다.

`input_sha256`는 request_id를 제외한 원본 내용·메타데이터에서 계산됩니다.
향후 Baseline과 AX가 같은 ReviewRequest/해시로 비교하도록 확장할 수 있지만 이번에는
Baseline이나 품질 평가기를 구현하지 않았습니다.

## 개발자가 구현할 핵심 함수 4개

모두 `src/career_ax/submission_review.py`에 있으며 현재 `NotImplementedError`를 냅니다.

| 함수 | 직접 구현할 내용 |
| --- | --- |
| `run_analyst(request: ReviewRequest, runner: AgentRunner) -> Assessment` | Analyst 프롬프트, runner 호출, Assessment 파싱 |
| `run_critic(request: ReviewRequest, runner: AgentRunner) -> Assessment` | 동일 원본의 독립 Critic 프롬프트와 runner 호출 |
| `reconcile_assessments(request, analyst, critic, verification) -> Assessment` | 검증 결과를 고려한 통합·충돌 처리 정책 |
| `decide_failure_action(error, *, role, attempt) -> Literal['retry', 'abort', 'human_review']` | 유한 재시도·중단·사람 검토 정책 |

처음 두 함수는 그래프 기본 hook입니다. 나머지 두 함수는 확장 지점으로만 정의했으며
아직 그래프에 연결하지 않았습니다. 실제 정책 구현 후 상태/분기와 테스트도 추가해야 합니다.
테스트는 대체 hook과 Fake Runner를 주입해 실행하며 미구현 함수를 통과 처리하지 않습니다.

```python
graph = build_review_graph(existing_runner, analyst=my_analyst, critic=my_critic)
result = graph.invoke({"request": request})["result"]
```

주입한 runner는 병렬 호출에 안전하고 역할 간 대화 이력을 공유하지 않아야 합니다.
실제 모델 입력은 오직 request를 allowlist 방식으로 직렬화하세요. 주입 코드의 외부 호출
여부는 자동 판정할 수 없어 `custom` 모드에서는 `real_llm_executed=null`로 기록합니다.
Codex의 저장소 코드 작성과 앱 런타임의 LLM 추론은 별개입니다. 이 CLI에는 실제 모델을
선택하는 옵션이 없으며, 기존 연구용 CodexRunner를 자동 활성화하지 않습니다.
실제 연결 시 기존 외부 전송 동의 조건을 유지하고 토큰·비용·타임아웃·실패 계측을 추가하세요.
원문을 지시로 실행하지 말고, tracing/관측 도구로 개인정보가 자동 전송되는 설정도 점검하세요.

## 저장 및 테스트 범위

기존 연구 Workflow, SQLite/PostgreSQL Store, DB migration, resume 동작은 유지됩니다.
이 그래프는 메모리 내 일회 실행이며 checkpointer를 설정하지 않았습니다. JSON/Markdown
출력은 연구 저장소나 그래프 체크포인터가 아닙니다. 영속화와 재개 설계는 후속 과제입니다.

`tests/test_submission_review.py`는 실제 병렬 barrier, 입력 동일성/격리, T0 유효성,
불변성, 인용 오류, TODO 예외, 실패 전파, CLI 출력을 검증합니다. 실제 LLM이나 프롬프트
품질 테스트는 포함하지 않습니다. 기존 GitHub Actions의 PostgreSQL 서비스/스키마 적용/
전체 pytest/연구 CLI smoke를 유지하고 합성 Submission Review smoke를 추가했습니다.

### 이번 변경의 로컬 검증 (2026-10-29)

- Python 3.12.3, LangGraph 1.2.18, Pydantic 2.14.1.
- 기존 코드 기준: 19 passed, PostgreSQL DSN 미설정으로 4 skipped.
- 최종 전체 테스트: 임시 PostgreSQL 17.11에 기존 migration을 적용하여 **47 passed, 0 skipped**.
- 새 Submission Review 계약/Stub 테스트: **24 passed**.
- SQLite 및 PostgreSQL 연구 Mock CLI: 각각 COMPLETE, 2 cycles, 9 calls.
- 기존 합성 eval-dev smoke: 3/3 통과. 새 Submission Review Stub CLI: JSON/Markdown 생성 확인.
- `pip check`, `git diff --check` 통과.
- 실제 모델 호출, 프롬프트/의미 검토 품질, Baseline 비교, 미구현 정책의 동작은 검증하지 않았습니다.
  로컬 테스트 결과는 GitHub Actions 실행 결과와 별개입니다.
