# Step 0: visibility-guard

## 배경 — 공개범위 계약을 지키는 문지기가 REST 하나뿐이다

`documents.visibility`는 `public`과 `private` 두 값만 갖는다고 코드 전체가 가정한다. 열람 술어
`backend/app/services/visibility.py:4`가 그 가정 위에 서 있고, ADR-027의 "볼 수 없는 문서는
존재하지 않는 것처럼 보인다"도 여기서 나온다.

**그런데 그 두 값을 강제하는 곳이 REST 라우터 한 곳뿐이다.**

| 자리 | 강제하는가 |
|---|---|
| `backend/migrations/002_tables.sql:20` | ❌ `DEFAULT 'public'`만 있고 CHECK 제약이 없다. 주석에 `-- public \| private`이라고 적혀 있을 뿐이다 |
| `backend/app/services/documents.py` `_insert_document` | ❌ `visibility`를 그대로 INSERT에 넘긴다 |
| `backend/app/api/documents.py:49`·`schemas.py`의 `CreateTextDocumentRequest` | ✅ pydantic `Literal["public", "private"]` |

같은 파일의 `content_type`은 사정이 다르다. `create_text_document`(`documents.py:153`)에 가드가
있고, 그 주석이 **이유를 명시적으로 적어두었다**:

> REST는 pydantic Literal이 먼저 422로 막아 이 가드에 닿지 않는다. 그래도 두는 것은
> **MCP 서버와 스크립트가 라우터를 거치지 않고 이 함수를 직접 부르기 때문**이다 — 코어가
> 자기 계약을 스스로 지킨다.

`visibility`만 그 원칙에서 빠져 있다. 이 step 다음(step 1)에서 MCP 쓰기 도구가 `visibility`를
인자로 받으면서, 그 주석이 예고한 상황이 실제로 발생한다.

**지금 상태에서 잘못된 값이 들어가면 무슨 일이 일어나는가.** 에러도 경고도 없이 저장되고,
열람 술어 `(d.visibility = 'public' OR d.owner_id = %(user)s)`에서 `"internal"`이나 뒤에 공백이
붙은 `"public "`은 public이 아니므로 **소유자만 볼 수 있는 문서가 된다.** 보안 사고는 아니지만
(fail-closed) 오타 하나로 문서가 조용히 사라지고, 계약이 두 값만 가정하는데 데이터는 세 번째를
허용하는 상태가 남는다.

## ✅ 이미 닫힌 결정 — 다시 판단하지 말고 아래대로 진행하라

### 결정 1 — 가드는 `_insert_document` 한 곳에 둔다

`visibility`가 값을 얻는 경로는 **문서 생성뿐이다.** 편집·태그 변경 엔드포인트는 이 컬럼을
건드리지 않으며, `visibility`를 바꾸는 엔드포인트는 존재하지 않는다(직접 확인하라 —
`grep -n "visibility" backend/app/services/documents.py`). 그리고 두 공급 경로
(`create_document` 파일 업로드 · `create_text_document` 텍스트)가 모두 `_insert_document`를
통과한다. 따라서 그 한 곳이 전부를 덮는다.

`create_document`와 `create_text_document`에 각각 가드를 넣지 마라. 같은 검사가 두 벌이 되고,
세 번째 공급 경로가 생기면 또 빠뜨린다.

### 결정 2 — REST 라우터와 스키마는 변경 0줄이다

pydantic `Literal`이 먼저 422로 막으므로 라우터에 예외 매핑을 추가해도 **도달할 수 없는
코드**가 된다. 이 프로젝트의 직전 작업(m11-a)에서 이미 인증이 걸린 라우터 위에 도달 불가능한
가드를 한 겹 더 얹은 사례가 있었다. 같은 실수를 반복하지 마라.

`app/api/documents.py`와 `app/api/schemas.py`를 **열어서 확인만 하고, 고치지 마라.**

### 결정 3 — DB CHECK 제약은 이번에 추가하지 않는다

가장 근본적인 자리는 DB지만 이번 범위 밖이다. 이유는 셋이다.

1. 이 phase의 scope는 `api`와 `mcp`다. 마이그레이션을 넣으면 db 레이어까지 넓어진다.
2. 기존 데이터에 두 값 외의 행이 있는지 사전 검사가 필요하다. VM DB는 데모 실행 이력이 있어
   로컬 컨테이너와 상태가 다르다.
3. 서비스 가드만으로 실효 방어가 완성된다 — 애플리케이션이 이 컬럼에 쓰는 경로가
   `_insert_document` 하나이기 때문이다.

**새 마이그레이션 파일을 만들지 마라.** 이 판단의 근거와 남은 위험은 step 2에서 ADR에 적는다.

### 결정 4 — 허용 값 목록은 `services/visibility.py`에 둔다

`VISIBLE_TO_USER` 술어가 사는 파일이 열람 의미론의 정본이다. 값 목록이 술어와 떨어져 있으면
한쪽만 바뀌는 상태가 가능해진다.

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `backend/app/services/documents.py` — **이 step의 유일한 주 수정 대상.**
  `TEXT_CONTENT_TYPES`(:23)와 예외 클래스들(:33-58), `create_document`(:114),
  `create_text_document`(:142), `_insert_document`(:171)를 전부 읽어라.
  특히 `create_text_document:153-157`의 `content_type` 가드가 **이 step이 따를 본보기**다
- `backend/app/services/visibility.py` — 전체가 4줄이다. 허용 값 목록이 들어갈 자리
- `backend/app/api/documents.py`, `backend/app/api/schemas.py` — **읽되 고치지 마라**(결정 2).
  pydantic `Literal`이 어디서 어떻게 막는지 확인용
- `backend/migrations/002_tables.sql` — `documents` 테이블 정의. CHECK 제약이 없다는 사실을
  직접 확인하라
- `backend/tests/test_documents.py` — 서비스를 직접 호출하는 테스트의 본보기. 여기에 쓴다
- `backend/tests/test_documents_api.py`:56 `test_text_ingest_matches_upload_pipeline_derivatives`,
  :122 `test_text_ingest_uses_authenticated_owner_and_normalizes_metadata` — REST 경로의 기존
  계약. 이 step이 깨뜨리면 안 되는 것
- `docs/ADR.md` 의 **ADR-035**(텍스트 공급 API — 결정 1의 "코어가 자기 계약을 스스로 지킨다")와
  **ADR-027**(열람 의미론이 두 값 위에 서 있다는 근거)

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_documents.py`

서비스를 직접 호출한다(REST를 타지 마라 — pydantic이 먼저 막아 가드에 닿지 않는다).

- `create_text_document(..., visibility="internal")` → `InvalidVisibility` 발생
- `create_document(..., visibility="internal")` → 같은 예외. **파일 업로드 경로도 막힌다**는
  것을 별도로 단언한다. `_insert_document`에 가드를 두었다는 결정이 여기서 검증된다
- `visibility="public "`(뒤 공백) 같은 근접 오타도 거부된다. 값을 임의로 `strip()`해서
  통과시키지 않는다 — 태그와 달리 공개범위는 조용히 교정할 대상이 아니다
- **거부된 요청이 문서를 저장하지 않는다.** 예외 발생 후 `documents` 행 수가 그대로임을
  단언한다 (기존 `test_text_ingest_rejects_invalid_content_without_saving`과 같은 형태)
- `visibility="public"`·`"private"`은 그대로 통과해 저장된다 (대조군)

**이 시점에 실행하면 실패한다. 그게 정상이다.**

### 2) `backend/app/services/visibility.py`에 허용 값을 추가한다

```python
VISIBILITY_VALUES: tuple[str, ...] = ("public", "private")
```

`VISIBLE_TO_USER` 술어 문자열은 **바꾸지 마라.** 이 상수로 SQL을 조립하지도 마라 — 술어는
`'public'`을 리터럴로 갖는 것이 맞고, 상수화하면 SQL 조립만 복잡해진다.

### 3) `backend/app/services/documents.py`에 가드를 넣는다

시그니처만 제시한다. 내부 구현은 재량이다.

```python
class InvalidVisibility(Exception):
    """공개범위가 열람 술어가 아는 두 값(public, private) 밖인 경우."""
```

`_insert_document` 안, **INSERT를 실행하기 전에** 검사한다. 기존 두 검사(빈 텍스트,
크기 초과)와 같은 자리·같은 방식이다. 예외 메시지는 사용자에게 그대로 보일 수 있으므로
한국어로 쓰고, 허용되는 값이 무엇인지 알려라.

## Acceptance Criteria

```bash
cd backend

# 1) 이 step이 추가한 가드
.venv/bin/pytest tests/test_documents.py -q
#   → 전부 passed

# 2) 두 공급 경로의 기존 계약이 살아 있다 (회귀 검출기)
.venv/bin/pytest tests/test_documents_api.py tests/test_triggers.py -q
#   → 전부 passed

# 3) 라우터·스키마·마이그레이션을 건드리지 않았다 (결정 2·3)
git diff --stat HEAD -- app/api/ migrations/
#   → 출력이 비어 있어야 한다 (변경 없음)

# 4) MCP 서버는 아직 손대지 않았다 — step 1의 작업이다
git diff --stat HEAD -- mcp_server/
#   → 출력이 비어 있어야 한다

# 5) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 가드가 `_insert_document` **한 곳**에만 있는가? (결정 1 — 같은 검사가 두 벌이면 실패)
   - `app/api/` 아래를 고치지 않았는가? (결정 2 — AC 3번이 이것을 잡는다)
   - 새 마이그레이션 파일을 만들지 않았는가? (결정 3)
   - 열람 술어 `VISIBLE_TO_USER` 문자열이 그대로인가? 경계는 쓰기 시점에만 추가됐고
     조회 의미론은 바뀌지 않았다 (ADR-027)
   - 서비스가 HTTP를 알지 못하는가? `HTTPException`을 import하지 않았는지 확인하라
     (`tests/test_architecture.py:51`이 이것을 검사한다)
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 2에서 일괄 처리).
4. 결과에 따라 `phases/m11d-mcp-write/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **잘못된 `visibility`를 기본값으로 교정하지 마라** (`"internal"` → `"public"`). 이유:
  공급자가 의도한 공개범위를 서버가 추측해서 바꾸면, 실수로 비공개를 의도한 문서가 공개될 수
  있다. 모르는 값은 거부한다.
- **`visibility` 값을 `strip()`·`lower()`로 정규화하지 마라.** 이유: `normalize_tags`가
  태그에 그렇게 하는 것은 태그가 자유 입력이기 때문이다. 공개범위는 두 값 중 하나를 고르는
  열거형이고, 근접 오타는 거부되어야 공급자가 실수를 안다.
- **`app/api/documents.py`·`schemas.py`에 예외 매핑을 추가하지 마라.** 이유: 결정 2.
  pydantic이 먼저 422로 막아 도달할 수 없는 코드가 된다.
- **마이그레이션 파일을 만들지 마라.** 이유: 결정 3. 이 phase의 scope 밖이고, 기존 데이터
  사전 검사가 선행돼야 한다.
- **MCP 서버(`backend/mcp_server/server.py`)를 고치지 마라.** 이유: step 1의 작업이다.
  이 step에서 함께 고치면 "MCP 도구 추가에 코어 변경이 몇 줄 필요했는가"라는 이 phase의
  핵심 실측이 섞여서 측정 불가능해진다.
- **기존 테스트를 깨뜨리지 마라.**
