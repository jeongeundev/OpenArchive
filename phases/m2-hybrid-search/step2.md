# Step 2: 문서 업로드·조회 API

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"API 설계" 절의 표**(`POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}` 행) · **"빈 파싱 결과 처리" 절** · "애플리케이션이 담당하는 복구 로직" 절의 API 항목 · "디렉토리 구조" 절의 `app/api/` 위치
- `/docs/PRD.md` — 핵심 기능 1(업로드 코드에 임베딩 호출이 없다) · **MVP 제외 사항의 인증 항목**(`X-User-Id` 헤더 기반 데모 사용자)
- `/docs/ADR.md` — **ADR-001**(트랜잭셔널 아웃박스) · **ADR-012**(마이그레이션은 API 서버만 실행) · **ADR-017**(원본 파일을 보관하지 않는다)
- `/CLAUDE.md` — **"임베딩 파이프라인의 트리거링은 반드시 DB 계층에서 처리한다. 애플리케이션 코드에서 `embedding_jobs`에 직접 INSERT 하지 마라"**
- **이전 step 산출물**:
  - `/backend/app/services/parsing.py`(step 0) — `detect_content_type`·`extract_text`, 예외 클래스. **빈 추출 결과는 예외가 아니라 빈 문자열이다**
  - `/backend/app/services/search.py`(step 1) — 이 step에서 쓰지는 않지만 서비스 계층의 코드 스타일 참고
  - `/backend/tests/conftest.py`(step 1에서 헬퍼가 추가됨) — `migrated_db` 픽스처와 문서 생성·임베딩 처리 헬퍼
- `/backend/app/db.py`, `/backend/app/main.py`, `/backend/app/config.py` — 풀과 lifespan의 현재 모습
- `/backend/migrations/002_tables.sql`, `/backend/migrations/003_triggers.sql` — 컬럼과 **트리거가 무엇을 자동으로 하는지**

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 step은 **API 계층의 첫 실물**이다. 여기서 만드는 `api/deps.py`를 step 3·4·5가 그대로 재사용한다.

가장 중요한 성질: **업로드 핸들러에 임베딩 관련 코드가 한 줄도 없다.** `documents`에 INSERT하면 트리거가 버전 이력을 남기고, 잡을 만들고, `NOTIFY`를 쏜다. 이것이 "원본-벡터 정합성이 DB 안에서 보장된다"는 이 과제의 심사 핵심이며, 애플리케이션이 잡을 만들면 그 주장이 무너진다.

## 작업

### 1. `backend/app/api/deps.py`

```python
async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """요청 하나에 풀 커넥션 하나를 빌려준다."""


def require_user_id(...) -> str:
    """X-User-Id 헤더. 없으면 400. 쓰기 작업용."""


def optional_user_id(...) -> str | None:
    """X-User-Id 헤더. 없으면 None. 읽기 작업용."""
```

**사용자 식별 정책** (이 phase의 확정 결정):

- **쓰기**(업로드·수정·삭제·reembed)는 `X-User-Id`가 **필수**다. 없으면 400. `documents.owner_id`가 NOT NULL이므로 소유자 없는 문서를 만들 수 없다.
- **읽기**(목록·상세·검색)는 헤더가 **없어도 된다.** 없으면 `None`으로 두고, 그 결과 `owner_id = NULL`이 SQL에서 NULL(=false)로 평가되어 **public 문서만** 남는다. **파이썬에서 `if user_id is None:` 분기를 만들지 마라** — SQL의 3값 논리가 이미 정확히 그 일을 한다. 분기를 넣으면 쿼리가 둘로 갈라지고 한쪽에만 권한 필터가 빠지는 사고가 난다.

FastAPI는 `x_user_id: Annotated[str | None, Header()] = None` 형태의 파라미터를 `X-User-Id` 헤더에 자동으로 매핑한다.

### 2. `backend/app/db.py`에 커넥션 검사 추가

풀 생성 시 `check=AsyncConnectionPool.check_connection`을 준다. 죽은 연결을 **대여 시점에** 감지·폐기·재수립한다 (`ARCHITECTURE.md` "애플리케이션이 담당하는 복구 로직"). 페일오버 후 첫 요청이 끊긴 연결을 받는 것을 막는다.

**요청 핸들러의 `OperationalError` 1회 재시도는 여기서 만들지 마라** — M5(복구 데모)의 범위이며, 지금은 재시도를 검증할 페일오버 환경이 없다.

### 3. `backend/app/main.py` 수정

lifespan에서 마이그레이션 실행 **뒤에** 풀을 열고, 종료 시 닫는다. 라우터를 등록한다.

기존 `run_migrations` 호출과 `/api/health`는 그대로 둔다.

### 4. `backend/app/api/documents.py`

세 엔드포인트를 만든다. **`DELETE`·`PUT`·`reembed`는 step 3의 범위이므로 여기서 만들지 마라.**

#### `POST /api/documents` — multipart 업로드, 201

받는 것: 파일 + `title`(선택, 기본값은 확장자를 뺀 파일명) + `tags`(선택) + `visibility`(선택, 기본 `public`). `X-User-Id` 필수.

처리 순서:

1. `detect_content_type(filename)` — 지원하지 않는 확장자면 **400**, 메시지에 지원 목록을 담는다
2. `extract_text(data, content_type)` — `TextDecodeError`는 **400**
3. **추출 텍스트가 공백 제거 후 비면 400** (`ARCHITECTURE.md` "빈 파싱 결과 처리"):
   ```
   { "detail": "문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다." }
   ```
   여기서 "공백"은 **공백·탭·CR·LF·폼피드**다. DB의 `documents_content_not_blank` CHECK가 같은 문자셋을 쓴다 — 파이썬 `str.strip()`은 이들을 모두 제거하므로 그대로 쓰면 된다
4. `content_hash`를 **`sha256(content.encode("utf-8")).hexdigest()`**로 계산한다. 트리거의 변경 감지 기준이다
5. `documents`에 INSERT하고 `RETURNING`으로 필요한 값을 받는다
6. **원본 파일 바이트를 버린다** — 저장하지 않는다 (ADR-017)

**이 핸들러에 임베딩·청킹·`embedding_jobs` 관련 코드를 넣지 마라.** 트리거가 전부 한다. INSERT 후 `embedding_status`는 `pending`이고 잡 1건과 `document_versions` v1이 이미 존재한다 — 그것을 테스트로 확인하라.

#### `GET /api/documents` — 목록

- 권한 필터 필수: `(visibility = 'public' OR owner_id = %(user)s)`
- 선택 필터: `status`(= `embedding_status`) · `tag`
- `embedding_status`를 응답에 포함한다 (UI 배지용)
- `content`(본문 전체)를 목록 응답에 넣지 마라 — 문서 수만큼 곱해져 응답이 비대해진다

#### `GET /api/documents/{id}` — 상세

응답에 포함할 것 (`ARCHITECTURE.md` API 표):

- 문서 메타데이터 + `content`(추출 텍스트 전체)
- **텍스트 버전 목록** — `document_versions`에서 version과 생성 시각
- **청크 수**
- **청크 기준 버전** — `document_chunks.version`. 문서 버전과 다를 수 있으며, 그 차이가 "아직 재임베딩 전"을 뜻한다. 청크가 없으면 `null`

**권한**: `public`이 아니고 소유자도 아니면 **404**를 반환한다. 403이 아닌 이유는 403이 "그 문서가 존재한다"는 사실을 알려주기 때문이며, 검색·목록에서 아예 보이지 않는 것과도 일관된다.

### 5. Pydantic 응답 모델

`app/api/schemas.py`에 두든 라우터 파일 안에 두든 재량이다. `dict`를 그대로 반환하지 말고 모델로 고정하라 — 필드가 조용히 바뀌면 프론트엔드(M3)가 깨진다.

### 6. `backend/tests/test_documents_api.py` — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.**

`conftest.py`에 **테스트 DB를 가리키는 `TestClient` 픽스처**를 추가하라. 주의점:

- 기존 `client` 픽스처는 **lifespan을 돌리지 않는다**(의도된 것). DB를 쓰는 API 테스트는 `with TestClient(app)`로 lifespan을 태워야 풀이 열린다
- lifespan은 `get_settings().database_url`을 보므로, **테스트 DSN을 환경변수로 주입하고** `get_settings.cache_clear()`가 된 상태여야 한다(`_clear_settings_cache`가 autouse로 처리 중)
- `app/db.py`의 `_pool`은 **모듈 전역**이다. 테스트 간에 이전 DSN을 가리키는 풀이 남지 않도록 픽스처에서 `close_pool()`로 정리하라

최소 아래를 확인한다.

1. **업로드 성공** — txt 업로드가 201, 응답에 id와 `embedding_status="pending"`.
2. **트리거가 파이프라인을 기동했다** — 업로드 직후 DB에 `embedding_jobs` pending 1건과 `document_versions` v1이 존재한다. **API 코드가 만든 것이 아님을 이 테스트가 증명한다.**
3. **빈 파싱 결과 400** — 공백·개행뿐인 txt를 올리면 400이고, **`documents`에 행이 생기지 않는다.**
4. **지원하지 않는 확장자 400** — `.hwp` 업로드.
5. **UTF-8이 아닌 txt 400** — CP949로 인코딩한 바이트.
6. **`X-User-Id` 없는 업로드는 400.**
7. **`content_hash`가 sha256이다** — 업로드한 텍스트의 sha256과 DB 값이 일치한다. 트리거의 변경 감지가 여기에 걸려 있다.
8. **목록의 권한 필터** — 타인의 private 문서가 목록에 없다. **헤더 없는(익명) 요청은 public만 본다.**
9. **목록의 태그·상태 필터.**
10. **상세 응답** — 버전 목록·청크 수·청크 기준 버전이 들어 있다. 워커를 돌리기 **전에는** 청크 수 0, 청크 기준 버전 `null`이고, 돌린 **뒤에는** 각각 채워진다. (step 1에서 conftest에 넣은 임베딩 처리 헬퍼를 쓴다.)
11. **타인의 private 문서 상세는 404.**
12. **없는 id 조회는 404** — UUID 형식이 아닌 값은 422여도 무방하다. 어느 쪽인지 테스트로 고정하라.

## Acceptance Criteria

```bash
docker compose up -d              # 프로젝트 루트에서
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_documents_api.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 라우터가 `app/api/` 아래에 있는가?
   - **재사용 대상 로직(파싱)을 라우터에 복제하지 않고 `app/services/`를 호출하는가?** 반대로, 이 라우터에서만 쓰는 CRUD SQL을 굳이 `services/`로 빼지는 않았는가?
   - **업로드 핸들러에 `embedding_jobs`·임베딩·청킹 관련 코드가 없는가?**
   - 모든 조회 쿼리에 권한 술어가 있는가?
   - `user_id is None`을 파이썬에서 분기하지 않고 SQL에 맡겼는가?
   - 원본 파일 바이트를 저장하지 않았는가?
3. 결과에 따라 `phases/m2-hybrid-search/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **`deps.py`의 함수 이름과 시그니처, 라우터 등록 방식, 응답 모델 이름, conftest에 추가한 TestClient 픽스처 이름을 반드시 포함시켜라.** step 3·4·5가 전부 재사용한다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **`embedding_jobs`에 INSERT하지 마라.** 이유: CLAUDE.md의 CRITICAL 규칙이자 이 과제의 심사 핵심이다. 트리거가 한다.
- **업로드 핸들러에서 임베딩을 만들거나 워커를 호출하지 마라.** 이유: 같다. 업로드 코드에 임베딩 호출이 없다는 것이 PRD 핵심 기능 1의 주장이다.
- **원본 파일을 디스크·DB·오브젝트 스토리지에 저장하지 마라.** 이유: ADR-017.
- **`user_id is None`을 파이썬에서 분기해 쿼리를 둘로 나누지 마라.** 이유: SQL의 `owner_id = NULL`이 이미 정확히 그 일을 하며, 분기하면 한쪽에만 권한 필터가 빠지는 사고가 난다.
- **`PUT`·`DELETE`·`reembed`를 만들지 마라.** 이유: step 3의 범위다.
- **검색 엔드포인트를 만들지 마라.** 이유: step 4의 범위다.
- **인증·세션·JWT를 만들지 마라.** 이유: PRD MVP 제외 사항이다. `X-User-Id` 헤더 하나로 충분하다.
- **`app/services/search.py`·`parsing.py`를 수정하지 마라.** 이유: 이전 step의 산출물이며 이 step은 그것을 소비만 한다. 수정이 필요하다고 판단되면 그 사실을 summary에 적어라.
- **`app/services/documents.py`를 만들지 마라.** 이유: `services/`는 **여러 소비자가 공유하는** 로직을 담는다(검색은 REST+MCP, 파싱·청킹은 API+워커). 이 라우터에서만 쓰는 CRUD SQL을 한 겹 더 감싸면 호출을 한 번 더 타는 것 외에 얻는 것이 없다. 이슈 #7의 범위 목록에도 `services/search.py`만 있다.
- **요청 재시도 로직을 만들지 마라.** 이유: M5의 범위이며, 지금은 검증할 페일오버 환경이 없다.
- 기존 테스트를 깨뜨리지 마라.
