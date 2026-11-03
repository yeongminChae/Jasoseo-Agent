# Submission Review — Sprint 02

Python 3.11 이상에서 `python -m pip install -e '.[dev]'`로 설치합니다.

```sh
# 네트워크/모델 없이 합성 입력의 실행 배선 확인
career-ax submission-review --request fixtures/submission_review/request.json --runner stub --output-dir runs/stub

# 인증된 로컬 Codex CLI 환경 (먼저 codex login)
career-ax submission-review --request fixtures/submission_review/request.json --runner codex --allow-external-model --timeout-seconds 60 --output-dir runs/codex

# OPENAI_API_KEY를 런타임 환경에 안전하게 설정한 뒤 실행
career-ax submission-review --request fixtures/submission_review/request.json --runner openai --model gpt-4.1-mini --allow-external-model --output-dir runs/openai
pytest
```

실제 모델은 명시적인 `--allow-external-model` 없이는 실행되지 않습니다. 키를 명령 인자,
소스, Git에 기록하지 마세요. OpenAI 실행기는 표준 라이브러리 HTTPS로 고정된
`api.openai.com`의 Chat Completions API를 호출하며, JSON 응답을 Pydantic으로 검증합니다.
해당 모델/API 권한과 네트워크 허용이 있는 환경이 필요합니다. Stub은 합성 자료만 받습니다.
테스트의 FakeRunner는 외부 모델 호출 없이 성공·오류 응답을 주입합니다.

## 입력 및 실행 경계

START → Analyst/Critic (독립 병렬 요청) → Verifier → END 구조를 유지합니다.
두 역할은 같은 T0 스냅샷의 새 복사본을 받습니다. 상대방의 결과, 공유 대화 기록,
체크포인터, 연구 DB, 이전 분석 조회는 없습니다. 실행기 객체는 공유하지만 제공되는
실행기는 대화 상태를 저장하지 않습니다. 커스텀 실행기도 병렬 호출에 안전하고 무상태여야 합니다.

원본 질문과 지원서가 필수입니다. source purpose/phase allowlist와 extra 필드 거부로
posthoc/이전 AI 결과 채널을 차단합니다. text에 거짓으로 섞인 사후 정보를 의미적으로
탐지하지는 못하므로 입력 선별은 필요합니다. sources는 지시가 아닌 신뢰하지 않는 데이터입니다.
실제 개인정보는 private/, 산출물은 runs/에 보관합니다. 커밋된 fixture는 합성입니다.

## 검증 및 통합

원시 Assessment와 통합 Assessment를 모두 보존합니다. source ID, 1-based 행 번호,
비어 있지 않은 정확한 부분 인용을 검사합니다. 중복 Finding ID는 관련 항목을 모두 제외합니다.
인용 실패 항목은 통합 결과에서 제외되며 재호출하지 않습니다. 인용 일치는 의미적 근거나
현실의 사실을 증명하지 않습니다. 통합 Finding의 limits와 unresolved에 이 한계를 표시합니다.

동일 category/statement/kind/evidence는 정확 중복으로 통합합니다. 같은 category/evidence에
서로 다른 판단이 있으면 potential_conflict로 표시하고 자동 해결하지 않습니다. 그 외는 distinct입니다.
이는 보수적인 문자열 기반 분류입니다. 바꿔 쓴 동일 주장이나 다른 인용에 걸친 의미적 모순은
놓칠 수 있습니다. AI 합의는 정답이나 채용 결과를 증명하지 않습니다.

- COMPLETE: 두 역할 응답과 인용 검사가 완료됨. 사실 검증/지원서 안전 판정이 아님.
- PARTIAL_COMPLETE: 한 역할만 성공. 실패 역할과 성공 역할을 별도 기록하며 사람 검토 필요.
- HUMAN_REVIEW: 모두 실패, 인용 오류, 충돌 가능성 또는 발견 사항이 전혀 없음.
- STRUCTURAL_CHECKS_ONLY: 합성 Stub 배선 확인.

semantic_review_complete는 항상 false입니다. real_llm_executed는 실제 실행기에서 적어도
한 역할의 스키마 유효 응답을 받았을 때만 true이며, 실패한 요청의 모델 도달 여부는 알 수 없습니다.
커스텀 hook 실행 여부는 null입니다. 빈 findings는 안전하다는 뜻이 아닙니다.

타임아웃과 명시적인 일시적 HTTP 오류(408/429/일부 5xx, transport failure)만 1회 재시도합니다.
역할당 최대 2회, 전체 최대 4회입니다. 인증/입력/스키마/인용 오류는 무조건 재시도하지 않습니다.
Codex 비정상 종료는 원인을 추정해 재시도하지 않습니다. API 출력은 4096 토큰으로 제한하며
달러 비용 계측은 없습니다. JSON/Markdown은 고정 파일명 review.json/review.md로 저장됩니다.
같은 출력 디렉터리는 덮어쓰므로 실행별 디렉터리를 사용하세요. 완료/Stub은 종료 코드 0,
부분 완료/사람 검토/실행 불가는 2입니다. 기존 출력은 실패해도 삭제하지 않습니다.

## 이번 환경 검증 (2026-11-03)

- 전체 pytest: 54 passed, 4 skipped (PostgreSQL DSN 미설정).
- CodexRunner에 합성 fixture로 실제 실행을 시도했으나 두 역할 모두 AgentRuntimeError.
- 별도 최소 codex exec 검사도 `failed to initialize in-process app-server client:
  Read-only file system (os error 30)`로 종료. 현재 Cloud의 중첩 CLI 실행은 이 오류로 불가.
  CLI 존재는 확인했지만 모델 인증/응답 성공은 확인하지 못했습니다.
- OpenAI 키가 구성되지 않아 실제 API 호출은 수행하지 않았습니다. 인증 가능한 환경에서
  위 명령으로 재검증해야 합니다. HTTP/JSON 파싱과 오류 정책은 가짜 전송 테스트로 확인합니다.
- 실제 LLM 품질, 의미적 사실 검증, PostgreSQL 실서비스는 이번 환경에서 미검증입니다.
- 기존 SQLite/PostgreSQL 연구 Workflow/저장소/DB migration은 변경하지 않았습니다.

- SQLite 연구 Mock CLI: COMPLETE, 2 cycles, 9 calls. eval-dev: 3/3 통과.
- Submission Stub JSON/Markdown 출력, pip check, git diff --check 통과.

## Sprint 02 후속 검증 (기준 커밋 5750c96)

상태 우선순위는 HUMAN_REVIEW 사유(인용 오류, 양쪽 실행 실패, 빈 발견 목록,
충돌 가능성)를 먼저 확인하고, 그 사유가 없을 때 한 역할 실패를 PARTIAL_COMPLETE로
처리합니다. 따라서 한 역할이 실패하고 다른 역할이 허위 인용을 반환하면
HUMAN_REVIEW이며, 잘못된 인용의 Finding은 통합 결과에 포함되지 않습니다.
양쪽 역할에 대한 타임아웃+허위 인용 회귀 테스트를 추가했습니다.

추가 합성 실험은 다음 두 파일로 분리했습니다.

- 입력: `fixtures/submission_review/missing_cause_validation/request.json`
- 평가 전용 Gold: `fixtures/submission_review/missing_cause_validation/gold.json`

문항은 원인·해결 방법·검증 방법과 결과를 요구합니다. 답변은 물품 전달 지연 상황과
작업표/회의라는 해결 활동만 설명하며 원인 및 효과 검증 결과를 누락합니다.
Gold는 이 두 예상 결함과 해석상의 한계를 담고 있으며 모델 입력으로 읽지 않습니다.
FakeRunner 테스트로 두 역할에 request만 전달되는 것과 Gold 필드 거부를 확인했습니다.

```sh
python -m pytest
career-ax submission-review --request fixtures/submission_review/request.json --runner stub --output-dir runs/sprint02-validation/original
career-ax submission-review --request fixtures/submission_review/missing_cause_validation/request.json --runner stub --output-dir runs/sprint02-validation/experiment
```

검증 결과: **57 passed, 4 skipped** (PostgreSQL DSN 미설정). 두 Stub 실행 모두
STRUCTURAL_CHECKS_ONLY, real_llm_executed=false이며 인용 구조 검사와 JSON/Markdown 생성을
확인했습니다. 생성된 JSON은 ReviewResult 스키마로 다시 검증했습니다. git diff --check 통과.
산출물은 runs/ 아래에 보관하며 Git에 포함하지 않습니다.

이번 후속 검증에서는 실제 외부 LLM 호출이나 개인정보 전송을 하지 않았습니다.
Stub은 결함을 탐지하는 모델이 아니므로 새 Gold 대비 검출률/정확도는 측정하지 않았습니다.
실제 LLM의 두 누락 탐지 여부, 의미적 사실 검증, PostgreSQL 통합은 여전히 미검증입니다.
