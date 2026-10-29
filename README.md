# Jasoseo Agent — AX Pilot v0.2 (SQLite + PostgreSQL)

**상태:** 초기 AX Pilot 코드(2026-10-27)를 기반으로 PostgreSQL 저장 계층을 추가한 개발 버전. Python 기반 멀티 역할 연구 루프, 엄격한 원문 인용 게이트, SQLite 체크포인트, 제한된 재실행. **기본 Mock 모드에는 AI 모델이 전혀 호출되지 않는다.**

이 프로젝트는 취업 탈락 원인을 예측하지 않습니다. 최초 검토 입력과 당시 GPT 출력 및 사후 정정을 구분하고, 문서 결함과 검토 누락에 대한 *조사 및 검증 절차*를 재현하기 위한 Pilot입니다.

## Quick start

요구 사항: Python 3.11+, Pydantic 2.x, pytest (개발용). 로컬 프로젝트 폴더에서 실행하세요.

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
career-ax doctor
career-ax inventory --manifest fixtures/synthetic_question_swap/manifest.json
career-ax run \
  --goal '가상 GPT 검토 누락을 조사하고 첫 번째 결과로 다음 질문을 생성하라' \
  --runner mock \
  --manifest fixtures/synthetic_question_swap/manifest.json
pytest
```

Windows PowerShell에서는 여러 줄 bash 실행 예제 대신 한 줄로 입력하세요.

```powershell
career-ax run --goal "가상 GPT 검토 오류와 재발 방지 규칙을 조사하라" --runner mock --manifest fixtures/synthetic_question_swap/manifest.json
```

실행 시 `data/research.sqlite`에 상태, `runs/<run_id>.json`과 `.md`에 보고서를 보관합니다. 둘 다 `.gitignore`에 포함되어 있어 외부 Git 저장소에 커밋하지 않도록 기본 설정했습니다.

예상되는 Mock 결과(합성 테스트 파일): `COMPLETE`, 연구 사이클 **2개**, 역할 호출 **9회**, `real_llm_executed=false`. 첫 사이클의 문항 순서 대조 관찰 ID를 근거로 다음 질문을 만들고, 두 번째 사이클에서 개선 가설을 조사합니다. **이는 코드의 파이프라인 검증일 뿐 새 AI가 오류를 찾았다는 의미는 아닙니다.**

### 증거 부족 상태 직접 시험

```bash
career-ax run --goal '과거 문항 순서 누락을 조사하라' --runner mock --manifest fixtures/missing_evidence/manifest.json
```

관련 근거가 없으므로 `BLOCKED`, 0개 완성 사이클로 끝나야 합니다. 종료 코드 2는 이 보류·차단 상태를 명시합니다.

### 중단 후 재개

```bash
career-ax run --goal '검토 오류 연구' --runner mock --max-calls 5
# BUDGET_EXHAUSTED 결과 JSON의 run_id를 확인한 다음
career-ax resume --run-id <run_id> --runner mock --max-calls 20
```

실행 중 문제가 생겨도 완료된 사이클은 SQLite에 저장됩니다. **실패 중인 사이클의 호출은 재개 시 반복될 수 있습니다**(완벽한 exactly-once 실행은 아님). `resume`은 출처 파일 해시와 실행 모드가 동일할 때만 허용합니다.

### 실제 Codex CLI 실행 (로컬에서만, 명시적 승인)

현재 배포 환경에는 Codex CLI가 없어 실제 모델 호출은 검증하지 못했습니다. 설치 후 인증된 개인 개발 환경에서 다음처럼 실행할 수 있도록 어댑터를 구현했습니다.

```bash
codex login
career-ax run \
  --goal '가상 폼의 문항 순서 오류와 예방 절차를 근거로 조사하라' \
  --runner codex \
  --allow-external-model \
  --manifest fixtures/synthetic_question_swap/manifest.json
```

`--allow-external-model`은 로컬 발췌문을 모델로 전송할 수 있다는 명시적 승인입니다. **실제 자기소개서, PDF, 기업 지원자료는 개인정보 마스킹 후 사용하세요.** Codex 연결 시 5개의 역할(`lead_plan`, `collector`, `analyst`, `critic`, `lead_next`)을 작업별 새 Codex 실행으로 수행합니다. CLI 입력은 JSON 구조화 프롬프트, 출력은 `--output-schema`로 제한합니다. `.txt`/`.md` 텍스트만 현재 지원합니다. PDF/이미지 자동 추출, OCR 및 네트워크 크롤링은 구현하지 않았습니다.

외부 추론에 사용되는 모델/CLI 버전, 인증, 사용 한도에 따라 실행 결과가 달라질 수 있습니다. Codex 플랜 사용량과 API 직접 과금은 별개일 수 있습니다. 이 Pilot은 토큰/달러 과금을 계측하지 않으며, CLI 모드의 `max_calls`만 제한합니다.

## 실제 연구 자료 연결

1. 개인 PC의 **비공개** `private/evidence` 폴더에 `.txt`나 `.md` 원문을 보관합니다.
2. 각 문서가 **최초 검토 T0 때 실제 있었던 자료인지** 확인합니다. 사후 보고서를 원본처럼 분류하지 마세요.
3. 예시 형식으로 `private/evidence/manifest.json` 파일을 작성합니다.

```json
{
  "sources": [
    {"source_id": "LOCAL_T0_FORM", "path": "form_excerpt.txt", "provenance": "primary", "phase": "t0_input"},
    {"source_id": "LOCAL_POSTHOC_REPORT", "path": "audit_report.md", "provenance": "secondary", "phase": "posthoc"}
  ]
}
```

4. `career-ax inventory --manifest private/evidence/manifest.json`으로 대상 출처를 확인합니다. 기본 `--scope audit`는 `t0_input`(당시 입력)과 `t0_output`(당시 GPT 출력)을 비교하는 **사후 감사**이며 `posthoc`(나중 정정)은 제외합니다. 최초 검토 모델의 입력 자체를 재현하려면 `--scope replay_t0`로 실행해 오직 `t0_input`만 사용하세요. `--scope research`는 사후 정정까지 포함하므로 T0 재현이 아닙니다.
5. 최초 검토 원본과 모델 응답을 정확하게 재연하려면 원시 PDF/PNG·대화 이력의 특정 시점까지 비교한 텍스트 발췌 및 시점별 출처 매니페스트가 필요합니다. 사후 감사 보고서만으로 이를 대체할 수 없습니다.

## 구현 구조

```text
src/career_ax/
  cli.py         CLI / 외부 전송 승인
  evidence.py    출처 파일 범위, SHA-256, 키워드 검색, 원문 인용 확인
  models.py      Pydantic 입력/출력 계약과 Enum
  runner.py      AgentRunner, MockRunner, CodexRunner
  workflow.py    연구 Lead/Collector/Analyst/Critic 호출, 후속 질문 연결, 상태 머신
  verifier.py    근거 검사 및 후속 질문의 최소한의 연결 검증
  store.py       SQLite 원자적 사이클 저장과 에이전트 호출 시도 로그
  reporting.py   인용+가설+불확실성을 구분한 Markdown 보고서
fixtures/        SYNTHETIC TEST DATA ONLY
  synthetic_question_swap/
  missing_evidence/
tests/           실제 실행 가능한 pytest 단위/통합 테스트

docs/TECHNICAL_DECISIONS.md
```

## 반드시 이해할 Python 학습 포인트

- **`workflow.py / invoke()`**: 모든 역할 호출을 모델 독립적인 `AgentRunner.run`으로 전달합니다. 호출 전에 SQLite에 예약해 **실패한 호출도 호출 상한에 포함**합니다. 에러 종류에 따라 최대 1회 재시도합니다.
- **`models.py / Decision`**: Pydantic `@model_validator`로 `CONTINUE`는 다음 질문과 근거 ID를 갖도록 강제합니다.
- **`workflow.py / _cycles()`**: 첫 사이클의 인용이 검증된 finding ID만 다음 질문에 사용할 수 있습니다. 동일 질문 반복, 출처 불일치, 호출·사이클 상한을 Python이 차단합니다.
- **`evidence.py / verify_ref()`**: 원본에서 `source_id:line`의 `quote`가 실제 문자열로 존재하는지 확인합니다. 단, 해당 문장 해석이 참이라는 의미는 아닙니다.
- **`store.py / save_cycle()`**: 하나의 사이클 완료 결과를 단일 SQLite 트랜잭션으로 저장합니다. 재개 시 미완료 사이클을 새로 수행합니다.
- **`runner.py / CodexRunner`**: 에이전트별 독립 컨텍스트 실행이며 Critic에게 Analyst 초기 응답을 전달하지 않습니다. 입출력에 JSON Schema를 적용합니다.
- **`verifier.py`와 `tests/`**: 런타임 스키마·인용 검사와 사람이 검증한 정답 기반 **Eval**을 구별해야 합니다. 현재는 런타임 검증 및 Mock 통합 테스트이며, 새 LLM 검토 성능 평가는 **미실행**입니다.

## 실제 완료 범위와 미완료 항목

| 항목 | 상태 |
|---|---|
| 단일 명령 연구 실행 | Mock에서 완료 |
| 연결된 2사이클과 증거 ID 이어받기 | Mock에서 완료 |
| Collector·Analyst·Critic 독립 출력 | 인터페이스 + Mock 통합 검증 |
| 원문에 없는 인용/중복 finding ID 차단 | 자동 테스트 완료 |
| T0 입력/출력/사후 자료 분리 | 자동 테스트 완료 |
| SQLite 체크포인트와 resume | 자동 테스트 완료 |
| 실패 호출 재시도·상한 | 자동 테스트 완료 |
| Codex 실모델 추론 | **미실행 — CLI가 실행 환경에 없음** |
| R03/R04 실제 원시 자료의 첫 검토 재현 | **미실행 — Point-in-time 원시 파일이 현재 Pilot 입력에 연결되지 않음** |
| Golden Dataset 독립 AI Eval | **미실행** |
| 비용 달러 상한 / 토큰 계측 | **미구현** (`null`) |
| 전체 지원자 데이터 이관 | **범위 외** |

## 중요한 보안/방법론 주의 사항

- 소스는 기본적으로 읽기 전용이며, 에이전트가 임의로 원본을 편집하도록 허용하지 않습니다.
- 실제 지원서 원본, 주민등록번호, 연락처, 계정 정보, 지원 사이트 URL 토큰을 Git에 넣지 마세요.
- `--runner codex`는 개인 자료가 외부 AI 제공자로 전송될 수 있습니다. 최소 발췌/익명화, 사용 권한 및 접근 통제를 직접 확인하세요.
- 동일 모델의 Analyst와 Critic은 서로 다른 호출이라도 편향이 독립적이라는 뜻은 아닙니다. 검토자 추가 효과는 블라인드 Evals로 실측해야 합니다.
- `quote` 대조는 재현 가능한 **형식적 근거 검사**입니다. 의미상 주장 오류, 서류 탈락 원인, 검토 품질 향상은 입증하지 못합니다.

참고: 기존 v0.3 설계는 별도의 상위 설계 문서입니다. 이번 코드는 이를 모두 구현하는 것이 아니라 핵심 자동 연구 루프의 실행 가능성을 시험합니다.

### 합성 DEV 스모크 테스트 (별도 명령)

```bash
career-ax eval-dev
```

가짜 문항 순서 오류·정상 문항 순서·증거 부족의 **3개 합성 사례**를 자동 실행해 `runs/mock_smoke.json`에 결과를 저장합니다. 이 결과는 시나리오별 상태 전이와 게이트 동작을 확인한 것이지, 실제 모델의 오류 탐지율·오탐률 측정이 **아닙니다**. 실제 Golden Dataset v0.2와 Rubric v1.0을 이용하는 새 모델의 블라인드 평가는 별도 후속 단계입니다.

---

## PostgreSQL storage option (added for Jasoseo Agent)

The original SQLite `Store` is still the default and all previous CLI commands
continue to work. A compatible `PostgresStore` is now available for storing
research runs, cycles, and agent-call logs in PostgreSQL. Full applicant corpus
and Batch Audit schemas are **not yet implemented**.

See [PostgreSQL setup and limitations](docs/POSTGRESQL.md) for Docker Compose,
`CAREER_AX_DATABASE_URL`, migrations, and CI instructions. The repository
contains **synthetic fixtures only**; do not upload real applications to a
public GitHub repository.
