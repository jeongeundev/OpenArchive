# Step 2: related-api

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"API 설계"** 절의 엔드포인트 표와 **"관련 문서·태그 추천"** 절의 응답 계약
- `/docs/ADR.md` — **ADR-018**, **ADR-019**
- `backend/app/services/related.py` — **이전 step들의 산출물.** `find_related`, `suggest_tags`와 반환 데이터클래스
- `backend/app/api/documents.py` — 이 step이 라우트를 추가할 파일. 기존 라우트의 의존성 주입·권한 처리 패턴
- `backend/app/api/schemas.py` — 응답 모델을 추가할 파일
- `backend/app/api/search.py` — 얇은 라우터의 기준 (검증과 상태 코드 변환만 한다)
- `backend/app/main.py` — 도메인 예외 → 상태 코드 매핑이 한 곳에 모여 있다
- `backend/tests/test_documents_api.py`, `backend/tests/test_search_api.py` — API 테스트 관례(`db_client`, `upload_document`, `run_embedding_worker`)

라우터는 얇다. 실제 로직은 서비스에 있고 라우터는 요청 검증과 상태 코드 변환만 한다.

## 작업

문서 상세가 소비할 조회 엔드포인트 2개를 추가한다.

### 1) 응답 모델 (`backend/app/api/schemas.py`)

```python
class RelatedDocumentItem(BaseModel):
    document_id: UUID
    title: str
    tags: list[str]
    score: float

class IdenticalDocumentItem(BaseModel):
    document_id: UUID
    title: str

class RelatedResponse(BaseModel):
    items: list[RelatedDocumentItem]
    identical: list[IdenticalDocumentItem]
    based_on_version: int | None
    reason: str | None

class TagSuggestionItem(BaseModel):
    tag: str
    freq: int

class TagSuggestionsResponse(BaseModel):
    items: list[TagSuggestionItem]
    based_on_version: int | None
    reason: str | None
```

> ⚠️ **`schemas.py` 수정이 tdd-guard 훅에 막힐 수 있다.** 훅은 Python 파일마다 `backend/tests/test_*<모듈명>*.py`를 요구하는데 `test_schemas.py`가 없다. 막히면 우회하지 말고 **`backend/tests/test_schemas.py`를 먼저 만들어라** — 위 응답 모델이 `services/related.py`의 데이터클래스를 `model_validate`로 그대로 받아들이는지 검증하는 테스트를 담는다. 그 자체로 계약 테스트가 되므로 형식적인 껍데기가 아니다.

### 2) 라우트 (`backend/app/api/documents.py`)

```python
@router.get("/{document_id}/related", response_model=RelatedResponse)
async def get_related(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
    k: Annotated[int, Query(ge=1, le=MAX_K)] = 10,
) -> RelatedResponse: ...

@router.get("/{document_id}/tag-suggestions", response_model=TagSuggestionsResponse)
async def get_tag_suggestions(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> TagSuggestionsResponse: ...
```

### 핵심 규칙

**1) 대상 문서의 접근 권한을 먼저 확인한다.** 두 라우트 모두 서비스 호출 전에 `service.get_document(conn, document_id, user_id=user_id)`를 부른다. 존재하지 않거나 타인의 private 문서면 `DocumentNotFound`가 올라가고 `main.py`의 핸들러가 404로 바꾼다. 라우터에서 `try/except`로 다시 잡지 마라.

**2) 청크가 없어도 200이다.** 404·400이 아니다 — 문서는 존재하고 요청도 유효하다. "아직 색인 전"은 오류가 아니라 상태다.

```
청크 0건 → 200 { "items": [], "identical": [...], "based_on_version": null, "reason": "not_indexed" }
청크 있음 → 200 { "items": [...], "identical": [...], "based_on_version": 2, "reason": null }
```

**3) `k` 상한은 `app.services.search.MAX_K`를 import해 쓴다.** 숫자를 라우터에 하드코딩하지 마라 — `hnsw.ef_search`와의 관계가 한 곳에서만 관리되어야 한다.

**4) 읽기 전용 조회라도 `optional_user_id`를 쓴다.** 익명 요청(`X-User-Id` 없음)은 `user_id=None`으로 내려가 public 문서만 본다. 이것이 정상 동작이다.

## 테스트

`backend/tests/test_related_api.py`를 **먼저** 작성한다. 최소한 아래를 덮어야 한다.

- 임베딩이 끝난 문서에서 `GET /related`가 200이고 `items`가 점수순이며 `based_on_version`이 채워진다
- 업로드 직후(워커 미실행) 문서에서 `reason == "not_indexed"`, `items == []`, **`identical`은 계산된다**
- 같은 내용을 두 번 업로드하면 서로가 `identical`에 나타난다
- **타인의 private 문서에 대한 요청은 404**다 (두 엔드포인트 모두)
- 타인의 private 문서가 `items`·`identical`에 섞이지 않는다
- `GET /tag-suggestions`가 빈도순 태그를 반환하고, 이미 달린 태그는 빠진다
- `k`·`limit` 범위를 벗어나면 422

`db_client` 픽스처와 `run_embedding_worker(dsn)` 헬퍼를 쓴다. TestClient는 동기라 잡 처리 시점을 테스트가 직접 정해야 한다.

## Acceptance Criteria

```bash
cd backend
source .venv/bin/activate
pytest tests/test_related_api.py -q   # 전부 통과
pytest -q                             # 기존 테스트도 통과
ruff check .                          # 린트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 라우터가 얇은가? SQL이나 비즈니스 판단이 라우터에 새어 나오지 않았는가?
   - 예외 → 상태 코드 매핑이 `main.py` 한 곳에 있는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (엔드포인트 경로와 응답 필드명 포함 — 프론트엔드 step이 이 정보를 쓴다)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 라우터에서 SQL을 직접 쓰지 마라. 이유: 검색·관련 문서 쿼리가 한 곳에만 존재해야 REST와 MCP 결과가 일치한다 (ADR-008)
- 청크가 없다고 404·400·204를 반환하지 마라. 이유: 오류가 아니라 상태이며, 프론트가 안내 문구를 띄우려면 200 + `reason`이 필요하다
- `embedding_status`로 `not_indexed`를 판정하지 마라. 이유: 재임베딩 중에도 이전 청크로 정상 응답해야 한다 (서비스가 이미 청크 존재로 판정한다)
- 권한 확인을 서비스 안으로 옮기지 마라. 이유: step 0/1이 후보 필터만 책임지도록 경계를 정했다. 같은 판정이 두 곳에 생기면 어긋난다
- tdd-guard가 막는다고 훅을 수정하거나 우회하지 마라. 테스트를 먼저 작성하는 것이 정답이다
- 기존 테스트를 깨뜨리지 마라
