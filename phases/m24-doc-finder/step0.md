# Step 0: finder-service

`services/documents.py`의 문서 목록에 **제목 검색·문서 유형 필터·정렬**을 더하고, 같은 조건의 **건수**와 사용자에게 **보이는 태그 목록**을 돌려주는 함수를 만든다. 서비스 계층만 다룬다(라우터는 step 1).

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 이슈 #187의 「문서 목록 · 찾기」 부분이다. 폴더(같은 이슈의 나머지)는 다음 phase(m25-folders)가 한다. 이 phase에는 폴더가 없다.
- 기능명세서 시험항목(10/7 제출, 제출 뒤 수정 불가 — **문구가 구현 계약이다**):
  - 목록 상단 「제목 검색」에 "출장"을 입력하면 제목에 "출장"이 들어간 문서만 표시됨
  - 조건에 맞는 문서가 없으면 "조건에 맞는 문서가 없습니다."가 표시됨
  - 문서 유형 「HWP」를 고르면 HWP 문서만 표시됨
  - 태그 「보안」을 고르면 그 태그가 붙은 문서만 표시됨
  - 제목 검색어와 유형·태그 필터를 함께 걸면 모든 조건을 만족하는 문서만 표시됨
  - 정렬을 「제목순」으로 바꾸면 제목 가나다순으로 표시됨
  - 정렬이 「최근 수정순」(기본)이면 수정일 최신순으로 표시됨
- 현재 `list_documents`(658행 근처)는 열람 술어 + `embedding_status`·`extraction_status`·`tag` 필터를 한 쿼리로 걸고 `ORDER BY created_at DESC, id`, `LIMIT/OFFSET`이다. **건수를 세는 함수가 없다** — 화면은 `/progress`의 상태별 합계를 전체 수로 쓰는데, 필터가 붙으면 그 숫자는 틀린다.
- 문서 유형은 `documents.content_type`에 소문자 확장자로 저장된다(`pdf docx txt md hwp hwpx xlsx pptx png jpg jpeg` — `services/parsing.py`의 `SUPPORTED_CONTENT_TYPES`). "HWP"는 `content_type = 'hwp'`다(`hwpx`는 별개 유형).
- 「수정일」은 `documents.updated_at`이다. 텍스트 수정·태그 변경·열람 범위 변경 등이 `updated_at = now()`로 갱신한다.
- CLAUDE.md CRITICAL: 정형 필터는 **SQL 안에서** 건다 — DB에서 넓게 가져와 앱에서 거르지 마라. 볼 수 없는 문서는 존재하지 않는 것처럼 — 건수·태그 목록에도 열람 술어(`services/visibility.py`의 `VISIBLE_TO_USER`)를 똑같이 건다.
- `list_documents` 호출처: `api/documents.py`(목록), `cli.py:1124`(export, 전체), MCP `list_documents` 도구, 테스트 다수. 새 인자는 전부 키워드 기본값으로 더해 기존 호출을 깨지 않는다.

## 읽어야 할 파일

- `/docs/ARCHITECTURE.md`, `/docs/ADR.md` — ADR-018·027(열람 술어 하나), ADR-054(폴더, 다음 phase)
- `backend/openarchive/services/documents.py` — `SUMMARY_COLUMNS`, `list_documents`, `document_progress`
- `backend/openarchive/services/visibility.py` — `VISIBLE_TO_USER`(별칭 `d`, 바인딩 `%(user)s`)
- `backend/tests/test_documents.py` — 839~900행 근처의 목록 테스트와 픽스처(`documents_conn`)
- `backend/tests/test_visibility.py` — 열람 술어 테스트 선례

## 작업

### 1) 테스트 먼저 — `backend/tests/test_documents.py`에 추가

실제 DB(컨테이너)에 문서를 넣고 서비스 함수를 직접 부른다.

1. `title_query="출장"` → 제목에 "출장"이 든 문서만. 대소문자 무시(영문 `"Report"`로 `"report"`가 맞음).
2. 제목 검색어의 `%`·`_`는 **글자 그대로** 맞는다 — `title_query="50%"`가 "50% 할인"은 맞히고 "500 할인"은 맞히지 않는다, `"a_b"`가 "axb"를 맞히지 않는다.
3. `title_query`가 빈 문자열·공백뿐이면 조건 없음과 같다.
4. `content_type="hwp"` → HWP 문서만(`hwpx`는 제외).
5. `tag="보안"` + `content_type` + `title_query`를 함께 → 모든 조건을 만족하는 문서만.
6. `sort="title"` → 제목 가나다순(`"다"`, `"가"`, `"나"` 순으로 넣고 `"가","나","다"`로 나옴). 같은 제목은 `id`로 안정 정렬.
7. 기본(`sort` 생략) → `updated_at` 최신순: 먼저 만든 문서의 태그를 바꿔 `updated_at`을 갱신하면 그 문서가 맨 앞에 온다.
8. `count_documents`가 같은 조건의 `list_documents`(limit 없이) 길이와 같다 — 조건 없음·제목·유형·태그·조합 각각. 볼 수 없는 문서는 세지 않는다(다른 사용자의 제한 문서 1건을 넣고 확인).
9. `list_visible_tags`가 볼 수 있는 문서의 태그만, 중복 없이, 정렬해서 돌려준다 — 다른 사용자의 제한 문서에만 붙은 태그는 나오지 않는다.
10. `sort`에 허용 밖 값을 주면 `ValueError`(SQL 조각으로 끼워 넣지 않는다는 확인).

기존 목록 테스트 중 `created_at` 순서를 전제한 것이 있으면, **그 테스트가 지키려던 성질(예: 페이지가 겹치지 않음)을 유지하는 형태로** 기본 정렬에 맞춰 고친다. 단언을 지우거나 약하게 만들지 마라.

### 2) 구현 — `backend/openarchive/services/documents.py`

```python
DocumentSort = Literal["updated", "title"]

async def list_documents(
    conn, *, user_id=None, embedding_status=None, extraction_status=None, tag=None,
    title_query: str | None = None, content_type: str | None = None,
    sort: DocumentSort = "updated", limit=None, offset=0,
) -> list[dict]: ...

async def count_documents(
    conn, *, user_id=None, embedding_status=None, extraction_status=None, tag=None,
    title_query: str | None = None, content_type: str | None = None,
) -> int: ...

async def list_visible_tags(conn, *, user_id: str | None = None) -> list[str]: ...
```

- **목록과 건수의 WHERE는 한 곳에 정의한다**(모듈 상수 하나). 두 쿼리가 같은 조각을 쓰게 해 조건이 어긋나지 않게 한다.
- 제목 검색: `d.title ILIKE '%' || %(title)s || '%' ESCAPE '\'`, 값은 파이썬에서 `\`·`%`·`_`를 이스케이프한 뒤 넘긴다. 앞뒤 공백을 잘라 빈 값이면 `None`.
- 정렬: `sort` 값을 **고정된 ORDER BY 조각 사전**에서 고른다 — `"updated"` → `d.updated_at DESC, d.id`, `"title"` → `d.title, d.id`. 사용자 입력을 SQL에 문자열로 끼워 넣지 마라. 허용 밖 값은 `ValueError`.
- 태그 목록: 열람 술어를 건 `documents d`에서 `unnest(d.tags)`의 `DISTINCT`를 정렬해 반환한다.
- 기존 `tag` 필터(`%(tag)s = ANY(tags)`)는 그대로 쓴다.
- docstring에 기본 정렬이 「최근 수정순」(updated_at)으로 바뀐 이유(명세서 기본값)를 한 줄 남긴다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_documents.py tests/test_visibility.py tests/test_share_visibility.py tests/test_mcp_server.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 작업 중에는 바꾼 범위의 테스트만 돌린다(전체 검증은 CI가 한다).
2. mutant 확인 — 각각 되돌려 테스트가 실패하는지 본다. 통과하면 테스트를 보강한다:
   - 이스케이프 제거(`%`·`_` 그대로 넘김) → 테스트 2 실패
   - `count_documents`에서 제목 조건 빠뜨림 → 테스트 8 실패
   - 태그 목록에서 열람 술어 제거 → 테스트 9 실패
3. 아키텍처 체크리스트: 필터가 전부 SQL 안에 있는가, 열람 술어가 건수·태그에도 걸렸는가.
4. `phases/m24-doc-finder/index.json`의 step 0을 갱신한다.

## 금지사항

- 목록을 넓게 가져와 파이썬에서 거르거나 세지 마라. 이유: CLAUDE.md CRITICAL(정형 필터는 SQL 안에서) — 페이지 경계와 건수가 틀어진다.
- 건수를 `len(list_documents(...))`로 구하지 마라. 이유: 페이지 크기만큼만 오므로 전체 건수가 아니다.
- `sort`·`title_query`를 f-string으로 SQL에 넣지 마라. 이유: SQL 주입.
- 제목 검색에 인덱스·마이그레이션을 더하지 마라. 이유: 이 phase는 스키마를 바꾸지 않는다 — 실측으로 느린 것이 드러나면 그때 근거와 함께 더한다.
- 기존 테스트를 깨뜨리지 마라
