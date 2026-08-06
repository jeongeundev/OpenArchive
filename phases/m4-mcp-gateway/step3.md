# Step 3: tag-editing

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"API 설계"** 절
- `/docs/ADR.md` — **ADR-017**(편집 대상은 추출 텍스트), **ADR-019**
- `backend/migrations/003_triggers.sql` — **이 step의 안전성 근거.** 트리거가 언제 발화하는지 반드시 읽어라
- `backend/app/services/documents.py` — `_load_for_write`, `update_extracted_text`, `SUMMARY_COLUMNS`, 도메인 예외 4종
- `backend/app/api/documents.py` — 라우트를 추가할 파일
- `backend/app/api/schemas.py` — 요청 모델을 추가할 파일
- `backend/tests/test_documents_api.py` — 권한·상태 코드 테스트 관례

기존 편집 경로(`PUT /api/documents/{id}`)가 권한과 낙관적 잠금을 어떻게 다루는지 읽고, 무엇을 **따르지 않을지**를 아래 지시로 확인하라.

## 작업

문서의 태그를 교체하는 엔드포인트를 추가한다. 지금은 태그를 업로드 시점에만 설정할 수 있어, 문서 상세의 태그 추천을 클릭해도 적용할 경로가 없다.

### 1) 서비스 (`backend/app/services/documents.py`)

```python
async def update_tags(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str,
    tags: list[str],
) -> dict: ...
```

- 권한 확인은 기존 `_load_for_write`를 재사용한다 — 없는 문서·타인의 private는 `DocumentNotFound`, 타인의 public은 `DocumentAccessDenied`
- 태그 정규화: 각 태그를 `strip()` → 빈 문자열 제거 → **순서를 보존한 중복 제거**. 그 이상 하지 마라(소문자 변환·길이 제한 등). 이유: 업로드 경로도 정규화하지 않으므로 두 경로가 어긋난다
- `RETURNING {SUMMARY_COLUMNS}`로 갱신된 문서를 반환한다

### 2) UPDATE 문 — 이 step에서 가장 중요한 부분

```sql
UPDATE documents SET tags = %(tags)s, updated_at = now() WHERE id = %(id)s
```

**`SET` 절에 `content_hash`를 넣지 마라. `content`·`version`도 넣지 마라.**

이유: 트리거는 `AFTER INSERT OR UPDATE OF content_hash`로 걸려 있고, **값이 바뀌지 않아도 `SET` 절에 컬럼이 언급되면 발화한다**(PostgreSQL 동작). 발화하면 (a) `document_versions`에 같은 버전으로 이력 시도가 들어가고 (b) `embedding_status`가 `pending`으로 떨어지며 (c) 불필요한 재임베딩 잡이 생긴다. 태그만 바꿨는데 문서가 재임베딩되고 상태 배지가 `pending`으로 돌아가는 것은 명백한 버그다.

역으로, 태그 변경은 트리거를 발화시키지 않으므로 **`version`이 올라가지 않는다.** 이는 의도된 동작이다 — 텍스트 버전은 추출 텍스트의 이력이며 태그는 그 대상이 아니다 (ADR-017).

### 3) 낙관적 잠금을 두지 않는다

요청 본문에 `version`을 받지 마라. 이유: 태그 변경은 `version`을 올리지 않으므로 version 기반 잠금은 **태그 변경끼리의 충돌을 애초에 감지하지 못한다.** 감지하지 못하는 잠금을 두면 보호받는다는 착각만 준다. 마지막 쓰기가 이긴다(last-write-wins)를 받아들이고, 그 근거는 step 7에서 ADR로 기록한다.

### 4) 라우트 (`backend/app/api/documents.py`)

```python
class UpdateTagsRequest(BaseModel):   # schemas.py
    tags: list[str]

@router.put("/{document_id}/tags", response_model=DocumentSummary)
async def update_tags(
    document_id: UUID,
    body: UpdateTagsRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentSummary: ...
```

전체 교체(PUT) 시맨틱이다. `add`/`remove` 같은 부분 연산을 만들지 마라 — 프론트가 현재 태그 목록을 이미 들고 있으므로 교체 하나로 추가·삭제가 모두 표현된다.

> `schemas.py` 수정이 tdd-guard에 막히면 step 2에서 만든 `backend/tests/test_schemas.py`에 요청 모델 검증을 추가하라.

## 테스트

`backend/tests/test_documents_api.py`에 **먼저** 테스트를 추가한다. 최소한 아래를 덮어야 한다.

- 태그를 교체하면 응답과 이어지는 `GET /api/documents/{id}`에 반영된다
- **재임베딩이 유발되지 않는다**: 태그 변경 전후로 `documents.version`이 그대로이고, `embedding_status`가 `ready`에서 `pending`으로 떨어지지 않으며, `embedding_jobs`에 새 pending 잡이 생기지 않는다 — 이 세 가지를 DB에서 직접 확인한다
- `document_versions`에 새 행이 생기지 않는다
- 빈 문자열·공백만 있는 태그는 제거되고, 중복은 하나만 남으며, 순서가 보존된다
- 빈 배열을 보내면 태그가 모두 지워진다
- 타인의 public 문서 → 403, 타인의 private 문서 → 404, 존재하지 않는 문서 → 404, `X-User-Id` 없음 → 400

## Acceptance Criteria

```bash
cd backend
source .venv/bin/activate
pytest tests/test_documents_api.py -q   # 전부 통과
pytest -q                               # 기존 테스트도 통과
ruff check .                            # 린트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - UPDATE 문의 `SET` 절에 `content_hash`·`content`·`version`이 **없는가**? (이 step의 핵심)
   - 애플리케이션이 `embedding_jobs`나 `document_versions`에 직접 INSERT하지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (엔드포인트 경로·요청 본문 형태 포함 — 프론트엔드 step이 쓴다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `UPDATE documents SET ... content_hash ...`를 쓰지 마라. 이유: 값이 같아도 컬럼이 언급되면 트리거가 발화해 가짜 버전과 재임베딩 잡이 생긴다
- `embedding_jobs`·`document_versions`에 직접 INSERT하지 마라. 이유: 잡 생성과 이력 기록은 DB 트리거의 책임이다 (CLAUDE.md CRITICAL)
- 기존 `PUT /api/documents/{id}`의 요청 본문에 `tags`를 추가하지 마라. 이유: 추출 텍스트 편집과 메타데이터 수정은 다른 관심사이고, 낙관적 잠금 규칙도 다르다
- 태그 정규화를 과하게 하지 마라(소문자 변환, 특수문자 제거, 개수 제한 등). 이유: 업로드 경로가 하지 않는 정규화를 편집 경로만 하면 같은 태그가 두 형태로 갈라진다
- 마이그레이션 파일을 건드리지 마라. 이 step은 스키마를 바꾸지 않는다
- 기존 테스트를 깨뜨리지 마라
