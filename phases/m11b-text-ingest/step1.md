# Step 1: text-ingest-api

## 배경 — 문서가 들어오는 경로가 사람의 파일 업로드 하나뿐이다

`docs/PRD.md` §4는 플랫폼 판별 기준 셋을 정의하고 현재 상태를 이렇게 적는다: *"소비자 쪽 N은
증명되어 있다 — Web UI·REST·MCP가 같은 services 계층을 소비한다. **공급자 쪽 N은 아직
주장이다** — 문서가 들어오는 경로는 사람의 파일 업로드 하나다."* 그래서 프로그래매틱 공급은
여러 확장 후보 중 하나가 아니라 **플랫폼 주장의 첫 번째 증명 의무**다.

이 step이 그 첫 경로를 연다. 외부 프로세스가 파일 없이 JSON 텍스트만으로 문서를 공급하고,
파이프라인 파생이 웹 업로드와 **동일하게** 동작하는 것 — 이 동등성 단언이 이 step의 본체이며,
엔드포인트 코드는 그것에 비하면 부수적이다.

## 이전 step에서 만들어진 것 (step 0)

`backend/app/services/documents.py`에 텍스트 우선 진입점이 생겼다. 코드를 직접 읽고 정확한
시그니처를 확인한 뒤 쓰라. 대략 이렇다:

```python
TEXT_CONTENT_TYPES: tuple[str, ...] = ("txt", "md")


async def create_text_document(
    conn, *, title: str, content: str, content_type: str = "md",
    owner_id: str, tags: list[str] | None = None, visibility: str = "public",
) -> dict:
    """공급자가 이미 가진 텍스트로 문서를 만든다. 원본 파일이 없으므로 filename은 NULL이다."""
```

- 반환은 업로드 경로(`create_document`)와 **같은 형태**의 dict다 — `DocumentSummary`가 그대로
  받는다.
- 빈 텍스트 → `EmptyExtractedText`, 500K 초과 → `ExtractedTextTooLarge`,
  `txt`·`md` 밖의 유형 → `UnsupportedFileType`. 앞의 둘은 `backend/app/main.py`의 전역 예외
  핸들러가 **이미 400으로 매핑한다.**
- 어휘: `documents.content`의 정본 명칭은 **"문서 텍스트"**이고, 그중 파일 업로드 경로에서
  만들어진 것이 **"추출 텍스트"**다 (`filename IS NOT NULL`). 새로 쓰는 문구는 이 구분을
  지킨다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 요청은 `filename`도 `owner_id`도 받지 않는다

`filename`을 받으면 원본 파일이 없는 문서에 파일명이 생겨 어휘가 다시 무너진다. 출처
(provenance)를 담고 싶어도 지금 넣지 마라 — 귀속(누가 넣었나)과 출처(어디서 왔나)는 다른
개념이고 provenance는 외부 소스 연결 시점의 별도 스키마다 (`docs/PRD.md` §6 R6).

`owner_id`는 **세션에서만** 온다 (`require_user_id`). 주체 식별자를 요청 데이터로 받지 않는
것이 사칭 불가 원칙이다 (PRD §6 R4 — `X-User-Id` 제거가 그 사례). 요청 본문에 `owner_id`가
섞여 와도 무시되어야 하며, **그것을 테스트로 고정한다.**

### 결정 2 — 라우터에 도달 불가능한 가드를 얹지 마라

`content_type`은 pydantic `Literal["txt", "md"]`가 라우터 진입 전에 422로 막는다. 그러므로
라우터에 `except UnsupportedFileType` 블록을 **두지 마라** — 실행될 수 없는 코드이고, 읽는
사람에게 "여기로 올 수 있다"는 거짓 정보를 준다.

같은 이유로 인증 가드를 손으로 다시 쓰지 마라. `Depends(require_user_id)`가 401을 낸다.

### 결정 3 — 요청 본문 크기 가드를 라우터에 두지 않는다

이 결정은 실측 근거로 닫혔다. `fastapi/routing.py`의 요청 핸들러는 `body_bytes = await
request.body()`로 **본문을 먼저 읽은 뒤** `solve_dependencies(...)`를 호출한다. 따라서 라우터
함수 안이든 `Depends`든 `Content-Length` 검사는 이미 본문이 메모리에 올라온 뒤에 도는
장식이며, 아무것도 막지 못한다.

실효 경계는 서비스의 `MAX_EXTRACTED_TEXT_LENGTH`(500,000자)가 지킨다 — **그것이 400으로
거부되고 행이 저장되지 않음을 테스트로 단언하는 것**이 이 step의 몫이다. 전송 층 상한은
리버스 프록시·미들웨어의 몫이며 이번에 도입하지 않는다(판단은 step 5의 ADR-035에 기록한다).

### 결정 4 — `title` 검증을 새로 만들지 마라

`title`은 pydantic 필수 필드로 충분하다. 공백 제목·중복 제목에 대한 새 규칙을 만들지 마라 —
업로드 경로에도 같은 규칙이 없고, 요청받지 않은 검증을 추가하는 것이 된다.

## 읽어야 할 파일

- `docs/ARCHITECTURE.md` 「API 설계」 절 — 엔드포인트 표와 라우터 관례
- `docs/PRD.md` §4(경계)·§5 C1·C4·§6(Identity 요구사항) — 이 step이 해소하는 요구
- `backend/app/services/documents.py` — **step 0에서 바뀐 부분 전체.** 특히
  `create_text_document`와 예외 5종
- `backend/app/api/documents.py` — 수정 대상. `upload_document`(:41-74)의 예외 처리 관례와
  `edit_document`(:148-162)의 JSON 본문 라우터 관례
- `backend/app/api/schemas.py` — 요청/응답 모델 관례. `EditDocumentRequest`(:35)·
  `UpdateTagsRequest`(:44)가 요청 모델의 본보기
- `backend/app/api/deps.py` — `Connection` 별칭과 `require_user_id`
- `backend/app/main.py` — 도메인 예외 → 상태 코드 매핑이 전부 여기 있다
- `backend/tests/conftest.py` — `db_client`·`login_as`·`upload_document`·`run_embedding_worker`
- `backend/tests/test_documents_api.py` — API 테스트 관례 전량. 특히
  `test_upload_trigger_creates_job_and_initial_text_version`(:42),
  `test_document_link_and_backlink_endpoints_return_resolved_documents`(:56),
  `test_write_endpoints_share_visibility_aware_ownership_rules`(:480)
- `backend/migrations/002_tables.sql`·`003_triggers.sql`·`006_edges_tables.sql`·
  `008_edges_triggers.sql`·`010_links_tables.sql`·`011_links_triggers.sql` — **파생 4종이
  어디서 어떻게 만들어지는지.** 테스트가 무엇을 봐야 하는지 여기서 확인하라

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_documents_api.py`에 추가한다(새 파일을 만들지 마라 — 문서 API 계약은 이
파일이 본다).

**⚠️ 이 step의 핵심 테스트 — 파생 4종 동등성**

같은 본문을 (a) multipart 업로드와 (b) JSON 텍스트 공급으로 각각 넣고, 워커를 돌린 뒤
**파생 4종이 동일하게 생겼음**을 단언한다.

- `embedding_jobs` — 삽입 트리거가 만든 잡. 애플리케이션이 만들지 않는다
- `document_versions` — 초기 텍스트 버전 1행
- `document_edges` — 임베딩 완료 트리거가 만드는 관계
- `document_links` — 본문의 `[[제목]]` 위키링크

본문에 `[[...]]` 링크를 하나 넣어야 `document_links`가 생긴다. 링크 대상 문서를 미리 만들어
두면 해석까지 확인할 수 있다. 워커 실행은 `conftest.run_embedding_worker(migrated_db)`를 쓴다
(`test_documents_api.py`의 기존 용례를 그대로 따르라).

비교는 두 문서의 파생 상태를 **대조**하는 형태여야 한다. "각각 존재한다"만 단언하면 동등성이
아니라 존재 확인에 그친다. 최소한 잡 건수·버전 행 수·청크 수·링크 대상·edge 존재 여부가
경로에 따라 다르지 않음을 보여라.

**그 밖에 단언할 것**

- 201과 응답 본문의 `filename`이 **`null`**이다. `content_type`·`tags`·`visibility`가 요청대로다
- 요청 본문에 `"owner_id": "mallory"`를 섞어 보내도 문서 소유자는 **로그인한 사용자**다
  (사칭 불가, R4)
- 미인증 요청 → **401**
- `visibility: "private"`으로 공급한 문서는 다른 사용자의 목록·상세에서 보이지 않는다
- 공백뿐인 `content` → **400**이고 문서가 저장되지 않는다 (`SELECT count(*)`로 확인)
- 500,000자를 넘는 `content` → **400**이고 저장되지 않는다
- `content_type: "pdf"` → **422** (pydantic `Literal`이 막는다)
- 태그가 정규화된다 — 공백 제거·중복 제거·순서 보존
- **낙관적 잠금이 동일하게 적용된다**: 텍스트 공급으로 만든 문서를 `PUT /api/documents/{id}`로
  편집하면 버전이 오르고, 낡은 `version`으로 편집하면 **409**와 `current_version`이 온다

### 2) `backend/app/api/schemas.py`에 요청 모델을 추가한다

```python
class CreateTextDocumentRequest(BaseModel):
    title: str
    content: str
    content_type: Literal["txt", "md"] = "md"
    tags: list[str] | None = None
    visibility: Literal["public", "private"] = "public"
```

파일의 기존 배치 관례를 따라 문서 관련 모델 근처에 둔다. 응답 모델은 새로 만들지 마라 —
`DocumentSummary`가 그대로 쓰인다 (업로드 경로와 같은 응답 형태여야 한다).

### 3) `backend/app/api/documents.py`에 엔드포인트를 추가한다

```python
@router.post("/text", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def create_text_document(
    body: CreateTextDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentSummary:
```

- 라우터 본문은 서비스 호출과 `DocumentSummary.model_validate(document)` 두 줄이면 끝나야 한다.
  예외 매핑은 `main.py`의 전역 핸들러가 이미 한다.
- **경로 순서에 주의하라.** `@router.get("/{document_id}")`가 이미 있는 라우터다. `/text`는
  POST이고 기존 `/{document_id}` 경로들은 GET·PUT·DELETE라 실제 충돌은 없지만, 새 라우트는
  파일에서 `upload_document` 바로 아래(경로 상수가 모여 있는 위쪽)에 두어라.

## Acceptance Criteria

```bash
cd backend

# 1) 문서 API 계약 전량이 통과한다 (신규 + 기존 회귀)
.venv/bin/pytest tests/test_documents_api.py -q
#   → 전부 passed

# 2) 코어 진입점과 스키마 테스트가 안 깨졌다
.venv/bin/pytest tests/test_documents.py tests/test_schemas.py tests/test_architecture.py -q
#   → 전부 passed

# 3) 엔드포인트가 실제로 등록됐다
.venv/bin/python -c "
from app.main import app
routes = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, 'methods')}
assert ('/api/documents/text', ('POST',)) in routes, routes
print('POST /api/documents/text 등록 확인')
"
#   → "POST /api/documents/text 등록 확인"

# 4) 라우터에 도달 불가능한 가드가 없다
grep -n "UnsupportedFileType" app/api/documents.py
#   → upload_document 안의 기존 한 곳에만 나온다 (텍스트 라우터에는 없어야 한다)

# 5) 라우터에 SQL이 없다 (계층 규칙)
grep -rn "execute(\|cursor(" app/api/
#   → 아무것도 출력되지 않아야 한다 (grep 종료 코드 1)

# 6) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 라우터가 `embedding_jobs`에 직접 INSERT하지 않는가? (파생은 전부 트리거가 만든다)
   - 검색·조회 경로를 건드리지 않았는가? (이 step은 쓰기 경로만 추가한다)
   - 응답 모델이 업로드 경로와 같은 `DocumentSummary`인가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 일괄 처리).
4. 결과에 따라 `phases/m11b-text-ingest/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`services/` 아래 파일을 고치지 마라.** 이유: step 0이 코어를 이미 준비했다. 이 step에서
  코어가 또 바뀌면 PR이 보여야 할 수치("인터페이스 추가로 코어가 몇 줄 바뀌었나")가 오염된다.
  step 0이 무언가 빠뜨렸다고 판단되면 고치지 말고 `blocked`로 멈추고 사유를 적어라.
- **라우터에 `Content-Length` 검사나 본문 크기 가드를 넣지 마라.** 이유: FastAPI가 본문을 읽은
  뒤에 라우터·의존성을 실행하므로 실행돼도 아무것도 막지 못한다 (결정 3).
- **라우터에 `except UnsupportedFileType`을 넣지 마라.** 이유: pydantic `Literal`이 먼저 422로
  막아 도달할 수 없다 (결정 2).
- **`main.py`의 예외 핸들러를 고치거나 추가하지 마라.** 이유: `EmptyExtractedText`·
  `ExtractedTextTooLarge`는 이미 400으로 매핑되어 있다. 중복 매핑은 어느 쪽이 이기는지 읽는
  사람이 알 수 없게 만든다.
- **`POST /api/documents`(업로드)의 시그니처·동작을 바꾸지 마라.** 이유: 두 경로의 동등성이
  이 step의 증명 대상인데, 기준선을 함께 움직이면 비교가 무의미해진다.
- **프론트엔드를 건드리지 마라.** 이유: Web UI에서 텍스트를 직접 공급하는 화면은 이 이슈의
  범위가 아니다 (`docs/PRD.md` §4의 공급 경로 확장은 프로그래매틱 경로가 대상이다).
- **기존 테스트를 깨뜨리지 마라.**
