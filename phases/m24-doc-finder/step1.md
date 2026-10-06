# Step 1: finder-api

step 0의 서비스 함수를 REST로 연다. 목록 엔드포인트에 찾기 조건을 더하고, 같은 조건의 건수와 보이는 태그 목록 엔드포인트를 만든다. 라우터·스키마만 다룬다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 0이 `services/documents.py`에 만든 것:
  - `list_documents(..., title_query=None, content_type=None, sort="updated", limit, offset)` — 기본 정렬은 `updated_at` 최신순, `"title"`이면 제목순
  - `count_documents(..., title_query, content_type, tag, embedding_status, extraction_status) -> int` — 목록과 같은 WHERE
  - `list_visible_tags(conn, *, user_id) -> list[str]`
- 현재 `GET /api/documents`(`api/documents.py:134`)는 `status`·`extraction_status`·`tag`·`limit(1~100)`·`offset`을 받고 `list[DocumentSummary]`를 돌려준다. **응답 모양(배열)은 바꾸지 않는다** — 화면·테스트·사용자 CLI(#189 예정)가 배열을 기대한다.
- 인증: 읽기 엔드포인트는 `require_reader`(`api/deps.py`)를 쓴다. 새 엔드포인트도 같다.
- 라우트 순서: `/progress`처럼 고정 경로는 `/{document_id}`보다 **먼저** 등록해야 한다 — 뒤에 두면 UUID 검증(422)에 걸린다(기존 주석 참고).

## 읽어야 할 파일

- `backend/openarchive/api/documents.py` — 목록·progress 엔드포인트, 라우트 순서 주석
- `backend/openarchive/api/schemas.py` — `DocumentSummary`, `DocumentProgress`
- `backend/openarchive/api/deps.py` — `require_reader`
- `backend/openarchive/services/documents.py` — step 0에서 바뀐 함수
- `backend/openarchive/services/parsing.py` — `SUPPORTED_CONTENT_TYPES`
- `backend/tests/test_documents_api.py` — API 테스트 선례(로그인 세션 헬퍼)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_documents_api.py`에 추가

1. `GET /api/documents?q=출장` → 제목에 "출장"이 든 문서만.
2. `GET /api/documents?content_type=hwp&tag=보안&q=…` → 모든 조건을 만족하는 문서만.
3. `GET /api/documents?sort=title` → 제목순. `sort` 생략 → 최근 수정순.
4. `sort=bogus`·`content_type=exe` → 422.
5. `GET /api/documents/count?q=…&content_type=…&tag=…` → `{"total": n}`이고 같은 조건의 목록 길이와 같다. 다른 사용자의 제한 문서는 세지 않는다.
6. `GET /api/documents/tags` → 보이는 문서의 태그만 정렬된 배열.
7. 로그인하지 않으면 `count`·`tags` 모두 401(기존 읽기 경계와 같음).
8. `/count`·`/tags`가 `/{document_id}`로 잘못 라우팅되지 않는다(422가 아니라 200).

### 2) 구현

- `GET /api/documents`: 쿼리 `q: str | None`, `content_type: ContentTypeFilter | None`, `sort: Literal["updated","title"] = "updated"`를 더해 서비스에 넘긴다. `content_type`은 `SUPPORTED_CONTENT_TYPES`로 만든 `Literal`(또는 동등한 검증)로 받는다.
- `GET /api/documents/count` → `DocumentCount{total: int}`. 쿼리는 목록과 같은 필터(`status`·`extraction_status`·`tag`·`q`·`content_type`). `limit/offset/sort`는 받지 않는다.
- `GET /api/documents/tags` → `list[str]`.
- 두 엔드포인트는 `/{document_id}` 앞에 등록한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents_api.py tests/test_documents.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `/count`를 `/{document_id}` 뒤로 옮기면 테스트 8이 실패해야 한다. 라우터에서 `q`를 서비스에 넘기지 않으면 테스트 1이 실패해야 한다.
3. `phases/m24-doc-finder/index.json`의 step 1을 갱신한다.

## 금지사항

- `GET /api/documents`의 응답을 `{items, total}` 같은 객체로 바꾸지 마라. 이유: 배열을 기대하는 기존 소비자가 깨진다 — 건수는 별도 엔드포인트로 준다.
- 라우터에서 필터·정렬·건수를 계산하지 마라. 이유: 비즈니스 로직은 `services/`에 둔다(CLAUDE.md) — 라우터는 넘기기만 한다.
- 기존 테스트를 깨뜨리지 마라
