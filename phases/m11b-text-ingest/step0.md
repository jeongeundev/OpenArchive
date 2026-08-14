# Step 0: text-entrypoint

## 배경 — 코어의 문서 생성 경로가 파일 어휘에 묶여 있다

문서를 만드는 코드 경로는 `backend/app/services/documents.py:create_document` 하나뿐이고, 그
시그니처는 `filename: str` + `data: bytes`다. 내부는 이 순서로 간다.

```
detect_content_type(filename)   # 확장자가 유형을 결정한다
    → extract_text(data, ct)    # 바이트에서만 텍스트가 나온다
    → INSERT INTO documents
```

결과: **텍스트를 이미 가진 공급자는 존재하지 않는 파일을 지어내야 한다.**
`scripts/seed_demo.py:170-178`이 그 우회를 이미 하고 있다 —
`filename=f"{document.title}.md"`, `data=document.content.encode()`.

이 step은 그 우회를 API 표면으로 승격시키는 대신, **코어에 텍스트 우선 진입점을 만든다.**
다음 step에서 REST 엔드포인트가, 이후 이슈에서 MCP 쓰기 도구가 이 함수를 부른다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — `create_text_document` 신설, `create_document`는 파싱 어댑터로 축소

두 진입점이 각자 INSERT를 갖지 않는다. 공통 삽입 헬퍼 하나를 두고 두 진입점이 그것을 부른다.

```
create_text_document(title, content, content_type, ...)  ─┐
                                                          ├─→ _insert_document(...)  → INSERT
create_document(filename, data, ...)                     ─┘
  = detect_content_type → extract_text → 위로 위임
```

이유: INSERT SQL·빈 검사·초과 검사·`content_hash` 계산·태그 정규화가 두 벌이 되면, 이후
컬럼이 하나 늘 때마다 두 곳을 고쳐야 하고 정합성 계약이 갈라지는 자리가 생긴다.

### 결정 2 — 직접 공급된 문서의 `filename`은 NULL이다

`create_text_document`는 `filename` 인자를 **아예 받지 않는다.** 컬럼은 이미 NULL 허용이고
(`backend/migrations/002_tables.sql:14` — `filename text, -- 업로드된 원본 파일명 (출처 표시용).
파일 자체는 보관하지 않는다`), 프론트엔드도 이미 NULL을 다룬다
(`frontend/src/components/DocumentMeta.tsx:34`의 `document.filename ?? "—"`).

출처(어디서 왔나)를 담는 필드를 새로 만들지 마라. 귀속(누가 넣었나)과 출처는 다른 개념이고,
provenance는 identity 모델이 아니라 별도 스키마의 몫이며 외부 소스 연결 시점에 정한다
(`docs/PRD.md` §6 요구사항 R6).

### 결정 3 — 어휘: `documents.content`의 정본 명칭은 "문서 텍스트"다

`ADR-017`은 **원본 파일 / 추출 텍스트 / 텍스트 버전**을 구분해 쓰라고 정했다. 직접 공급된
텍스트는 무엇에서도 추출된 것이 아니므로 "추출 텍스트"라 부를 수 없다. 포함 관계로 정리한다.

- **문서 텍스트** (`documents.content`) — 정본 명칭. 상위 개념
- **추출 텍스트** — 문서 텍스트 중 파일 업로드 경로에서 만들어진 것. `filename IS NOT NULL`
- **텍스트 버전** (`document_versions`) — 기존 그대로

새 코드의 주석·예외 문구에서 이 구분을 지켜라. **기존 코드의 "추출 텍스트" 문구를 일괄
치환하지 마라** — 업로드 경로의 문구는 여전히 정확하다. 문서(`docs/`) 반영은 step 5가 한다.

### 결정 4 — 텍스트로 공급 가능한 유형은 `txt`·`md` 두 가지다

`pdf`·`docx`는 바이너리 형식이라 "텍스트를 직접 공급한다"가 성립하지 않는다. 서비스가
검증한다 — REST 라우터가 pydantic으로 막더라도, MCP 서버와 스크립트가 이 함수를 직접
부르므로 코어가 자기 계약을 지켜야 한다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `docs/ADR.md` 의 **ADR-017**(인라인 편집은 추출 텍스트의 새 논리 버전을 만든다),
  **ADR-031**(제품 정의를 플랫폼으로 확장한다) — 이 step의 어휘와 경계의 근거
- `docs/ARCHITECTURE.md` — 스키마·트리거·파이프라인 전반
- `backend/app/services/documents.py` — **이 step의 수정 대상 전체.** 특히 예외 5종(:32-56),
  `normalize_tags`(:98), `create_document`(:103-142)
- `backend/app/services/parsing.py` — `detect_content_type`·`extract_text`·
  `SUPPORTED_CONTENT_TYPES`·`UnsupportedFileType`
- `backend/migrations/002_tables.sql` — `documents` 테이블의 컬럼과 CHECK 제약
- `backend/migrations/003_triggers.sql` — 문서 삽입/변경 시 잡·버전 이력을 만드는 트리거
- `backend/tests/conftest.py` — fixture(`migrated_db`·`clean_db`)와 헬퍼
  (`insert_test_document`·`process_all_embedding_jobs`)
- `backend/tests/test_links.py`, `backend/tests/test_system.py` — **서비스 직접 호출 테스트의
  본보기.** `migrated_db`로 async conn을 만들고 서비스 함수를 키워드 인자로 부르는 구조
- `backend/tests/test_documents_api.py` — 깨뜨리면 안 되는 업로드 경로 계약 전량

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_documents.py`(신규)를 만든다. **문서 서비스를 직접 호출하는 테스트 파일은
지금 없다** — 지금까지 API를 통해서만 검증해 왔다. 이 파일이 코어 진입점의 계약을 본다.

`test_links.py`의 구조를 그대로 따른다 — `migrated_db`에서 자체 async conn fixture를 만들고,
서비스 함수를 키워드 인자로 부른다.

최소 아래를 단언한다:

- `create_text_document`가 만든 문서는 `filename`이 **NULL**이고, `title`·`content`·
  `content_type`·`owner_id`·`visibility`가 준 값 그대로다
- 같은 문서에 대해 **파생이 파일 업로드와 동일하게 생긴다** — `embedding_jobs`에 잡 1건,
  `document_versions`에 초기 버전 1건. 애플리케이션이 만들지 않고 트리거가 만든다
- `content_hash`가 `sha256(content)`와 일치한다
- 공백뿐인 `content` → `EmptyExtractedText`가 오르고 **행이 저장되지 않는다**
  (`SELECT count(*)`로 확인. 예외만 잡고 끝내지 마라)
- `MAX_EXTRACTED_TEXT_LENGTH`를 넘는 `content` → `ExtractedTextTooLarge`, 역시 미저장
- `content_type="pdf"` → `UnsupportedFileType`, 미저장
- 태그가 정규화된다 — 공백 제거·중복 제거·순서 보존 (`normalize_tags`와 같은 규칙)
- **회귀 검출**: `create_document`(파일 경로)가 여전히 `filename`을 저장하고 같은 파생을
  만든다. `title`을 주지 않으면 파일명 stem이 제목이 되는 기존 동작도 그대로다

**이 시점에 실행하면 `ImportError`로 실패한다. 그게 정상이다.**

### 2) `backend/app/services/documents.py`를 고친다

시그니처만 제시한다. 내부 구현은 재량이다.

```python
TEXT_CONTENT_TYPES: tuple[str, ...] = ("txt", "md")


async def create_text_document(
    conn: psycopg.AsyncConnection,
    *,
    title: str,
    content: str,
    content_type: str = "md",
    owner_id: str,
    tags: list[str] | None = None,
    visibility: str = "public",
) -> dict:
    """공급자가 이미 가진 텍스트로 문서를 만든다. 원본 파일이 없으므로 filename은 NULL이다."""
```

- 반환은 `create_document`와 **같은 형태**(`SUMMARY_COLUMNS` dict). 호출부가 두 경로를 구별할
  필요가 없어야 한다.
- `content_type`이 `TEXT_CONTENT_TYPES` 밖이면 `UnsupportedFileType`을 던진다. 새 예외 클래스를
  만들지 마라 — 메시지로 상황을 표현한다 (예: `"텍스트로 공급할 수 있는 유형은 txt, md입니다."`).
- 빈 문서 텍스트의 예외 문구는 업로드 경로와 **달라야 한다.** 업로드는 추출 실패
  (`"문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다."`)이고,
  직접 공급은 빈 입력이다. `EmptyExtractedText`의 docstring이 이미 그 설계를 적어 두었다 —
  예외가 문구를 나른다.

공통 삽입 헬퍼는 이 형태를 권한다(이름·인자 구성은 재량):

```python
async def _insert_document(
    conn: psycopg.AsyncConnection,
    *,
    title: str,
    filename: str | None,
    content_type: str,
    content: str,
    owner_id: str,
    tags: list[str] | None,
    visibility: str,
    empty_message: str,
) -> dict:
```

`create_document`는 이렇게 줄어야 한다: `detect_content_type` → `extract_text` →
`title or PurePath(filename).stem` 계산 → 헬퍼 위임. **자기 자신의 INSERT를 갖지 않는다.**

## Acceptance Criteria

```bash
cd backend

# 1) 새 서비스 테스트가 통과한다
.venv/bin/pytest tests/test_documents.py -q
#   → 전부 passed

# 2) 업로드 경로 계약이 안 깨졌다 (이 step의 회귀 검출기)
.venv/bin/pytest tests/test_documents_api.py tests/test_seed.py tests/test_mcp_server.py -q
#   → 전부 passed

# 3) INSERT가 한 곳뿐이다
grep -c "INSERT INTO documents" app/services/documents.py
#   → 1

# 4) 텍스트 진입점이 filename 인자를 받지 않는다
grep -n "filename" app/services/documents.py
#   → create_document / _insert_document 계열에만 나온다.
#     create_text_document 시그니처 안에는 없어야 한다 (눈으로 확인)

# 5) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/services/documents.py`에 fastapi/starlette import가 없는가? (서비스는 HTTP를 모른다)
   - `embedding_jobs`·`document_versions`·`document_edges`·`document_links`에 애플리케이션이
     직접 INSERT하지 않는가? (`tests/test_architecture.py`가 이것을 단언한다)
   - 벡터 컬럼·차원·마이그레이션을 건드리지 않았는가? (이 step은 스키마 변경이 없다)
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11b-text-ingest/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **REST 엔드포인트를 만들지 마라.** 이유: `POST /api/documents/text`는 step 1의 작업이다.
  여기서 함께 하면 코어 변경과 인터페이스 추가가 한 커밋에 섞여, PR에서 "services가 몇 줄
  바뀌었나"를 분리해 보여줄 수 없게 된다.
- **마이그레이션 파일을 만들지 마라.** 이유: `filename`은 이미 NULL 허용이고
  (`002_tables.sql:14`), 이 step에 스키마 변경은 필요 없다.
- **`documents` 테이블에 출처(provenance) 컬럼을 추가하지 마라.** 이유: 귀속과 출처는 다른
  개념이고 provenance 스키마는 외부 소스 연결 시점의 결정이다 (PRD §6 R6).
- **`scripts/seed_demo.py`를 고치지 마라.** 이유: step 3의 작업이다. 여기서 건드리면 코어
  변경의 회귀와 seed 동작 변경이 한 커밋에 섞인다.
- **`extract_text`·`detect_content_type`을 고치지 마라.** 이유: 이 두 함수는 파일 파싱 전용
  순수 함수이고, 텍스트 직접 공급은 파싱을 거치지 않는다. 여기에 텍스트 분기를 넣으면
  "파싱하지 않는 경로"가 파싱 모듈을 통과하는 모순이 생긴다.
- **기존 예외 클래스의 이름이나 기존 문구를 바꾸지 마라.** 이유: `main.py`의 예외 핸들러와
  `tests/test_documents_api.py`가 문구·상태 코드를 고정하고 있다.
- **기존 테스트를 깨뜨리지 마라.**
