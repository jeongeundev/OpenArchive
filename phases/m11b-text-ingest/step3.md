# Step 3: seed-text-ingest

## 배경 — 우회를 제도화하지 않았음을 코드로 보인다

`scripts/seed_demo.py`는 저장소 문서를 잘라 시연용 문서로 적재한다. 그 텍스트는 이미 메모리에
있는데, 문서를 만드는 유일한 경로가 파일 중심이었던 탓에 **존재하지 않는 파일을 지어내는
우회**를 써 왔다.

```python
await create_document(
    conn,
    filename=f"{document.title}.md",   # ← 이런 파일은 없다
    data=document.content.encode(),    # ← 텍스트를 바이트로 만들었다가 다시 텍스트로 되돌린다
    ...
)
```

step 0이 코어에 텍스트 우선 진입점을 만들었으므로 이 우회는 더 존재할 이유가 없다. 이 step은
저장소에서 우회를 **삭제**한다 — "우회를 API 표면으로 승격시키지 않았다"는 주장이 문장이 아니라
코드로 남는다.

## 이전 step에서 만들어진 것

- **step 0** — `backend/app/services/documents.py`에 `create_text_document(title, content,
  content_type, owner_id, tags, visibility)` 신설. `filename` 인자가 없고 컬럼은 NULL이 된다
- **step 1** — `POST /api/documents/text`
- **step 2** — `examples/ingest_text.py`(표준 라이브러리 전용 공급 클라이언트)

정확한 시그니처는 `backend/app/services/documents.py`를 **직접 읽어 확인하라.**

## ✅ 이미 닫힌 결정

### 결정 1 — seed 문서의 `content_type`은 `md`를 유지한다

저장소 문서는 Markdown이고, `content_type`은 검색 필터(`services/search.py`의
`d.content_type = %(ctype)s`)와 진단이 실제로 쓰는 값이다. 여기서 값이 바뀌면 시연 데이터의
검색 결과가 달라진다.

### 결정 2 — seed 문서의 `filename`이 NULL이 되는 것은 의도된 변화다

`filename`은 "업로드된 원본 파일명(출처 표시용)"이다(`backend/migrations/002_tables.sql:14`).
seed 문서에는 업로드된 원본 파일이 없으므로 NULL이 맞다. Web UI는 이미
`document.filename ?? "—"`로 처리한다(`frontend/src/components/DocumentMeta.tsx:34`).

> 참고: 이미 seed가 적재된 개발 DB의 기존 행은 바뀌지 않는다. `seed_documents`는 제목 기준
> 멱등이라 있는 문서를 다시 넣지 않기 때문이다. **그 행들을 UPDATE하는 코드를 작성하지
> 마라** — 이 step은 적재 경로를 고치는 것이지 데이터 마이그레이션이 아니다.

## 읽어야 할 파일

- `scripts/seed_demo.py` — **수정 대상 전체.** 특히 `SeedDocument`(:35-40),
  `load_seed_documents`, `seed_documents`(:158-181), import 절(:21-22)
- `backend/app/services/documents.py` — step 0 산출물. `create_text_document`의 시그니처와 예외
- `backend/tests/test_seed.py` — **깨뜨리면 안 되는 계약 전량.** 멱등성·비공개 문서 보존·
  위키링크·문서 개수 하한을 단언한다
- `backend/migrations/002_tables.sql` — `documents` 컬럼과 CHECK 제약
- `backend/migrations/011_links_triggers.sql` — 삽입 시점에 `document_links`를 만드는 트리거
  (진입점이 바뀌어도 같은 INSERT를 보므로 동작은 같아야 한다)

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_seed.py`에 추가한다:

- seed 적재 후 `owner_id = 'seed'`인 문서가 **전부 `filename IS NULL`**이다
- 같은 문서들의 `content_type`이 여전히 **`md`**다
- 위키링크 파생이 그대로다 — 적재 후 `document_links`에 행이 생기고, 저장소 seed에 들어 있는
  실제 ADR 제목 링크가 해석 대상으로 남는다. (기존
  `test_repository_seed_turns_adr_references_into_full_title_wikilinks`는 적재 없이 순수
  파싱만 본다. 여기서 필요한 것은 **적재 후 DB 상태** 확인이다)

**이 시점에 실행하면 `filename` 단언이 실패한다. 그게 정상이다.**

### 2) `scripts/seed_demo.py`를 고친다

- import를 `create_text_document`로 바꾼다
- `seed_documents`의 호출을 텍스트 진입점으로 교체한다. `filename`·`data`·`.encode()`가
  **한 글자도 남지 않아야 한다**
- `SeedDocument`에 `content_type` 필드를 새로 만들지 마라 — 전부 Markdown이고, 값이 갈릴
  이유가 지금 없다. 호출부에서 `content_type="md"`를 넘기거나 기본값을 그대로 쓴다

이 파일에서 그 밖의 것을 고치지 마라. 문서 수집·분할·위키링크 삽입 로직은 이 step의 범위가
아니다.

## Acceptance Criteria

```bash
cd backend

# 1) seed 테스트가 전부 통과한다 (신규 + 기존 회귀)
.venv/bin/pytest tests/test_seed.py -q
#   → 전부 passed

# 2) 코어·API 계약이 안 깨졌다
.venv/bin/pytest tests/test_documents.py tests/test_documents_api.py tests/test_architecture.py -q
#   → 전부 passed

# 3) 우회가 저장소에서 사라졌다
cd .. && grep -n "create_document\|\.encode()\|filename=" scripts/seed_demo.py
#   → create_text_document 호출만 보여야 한다.
#     "create_document(" 단독 호출·"filename=" 인자·"content.encode()"가 남아 있으면 실패다
#     (:147/:153의 filename은 파일 읽기용 지역 변수라 무관하다 — 눈으로 구분하라)

# 4) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `seed_demo.py`가 여전히 서비스 계층만 통해 문서를 만드는가? (직접 INSERT 금지)
   - `embedding_jobs`·`document_versions`·`document_links`에 스크립트가 직접 INSERT하지 않는가?
     (`tests/test_architecture.py`가 `scripts/`도 검사한다)
   - seed 문서 개수·태그·비공개 문서 구성이 그대로인가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11b-text-ingest/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **기존 seed 행을 UPDATE하는 코드를 쓰지 마라.** 이유: 이 step은 적재 경로 교체이지 데이터
  마이그레이션이 아니다. 개발 DB의 기존 행이 옛 `filename`을 갖는 것은 정상이다.
- **`SeedDocument`에 필드를 추가하지 마라.** 이유: 전부 Markdown이라 값이 갈릴 이유가 없다.
  쓰이지 않는 유연성을 넣는 것이 된다.
- **seed 문서의 제목·태그·비공개 구성·개수를 바꾸지 마라.** 이유: `test_seed.py`가 이 구성을
  단언하고, 시연 시나리오(고아 문서·깨진 링크·비공개 문서)가 여기에 의존한다.
- **`services/`·`api/`·`examples/`를 고치지 마라.** 이유: step 0~2가 확정했다. 여기서 다시
  건드리면 각 step이 무엇을 바꿨는지 PR에서 분리해 볼 수 없게 된다.
- **마이그레이션 파일을 만들거나 고치지 마라.** 이유: 스키마 변경이 필요 없고, 적용된
  마이그레이션 파일은 수정 대상이 아니다 (ADR-005).
- **기존 테스트를 깨뜨리지 마라.**
