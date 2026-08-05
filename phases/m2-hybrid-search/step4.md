# Step 4: 검색 API

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"검색 데이터 흐름" 절**(서비스가 무엇을 하는지) · "API 설계" 표의 `POST /api/search` 행 · **"프론트엔드 패턴" 절의 `/search` 화면**("실행된 SQL 보기" 토글 — 이 step이 그 데이터를 공급한다)
- `/docs/ADR.md` — **ADR-011 보강 4**(`ef_search` 상·하한과 `k` 제한의 근거) · **ADR-008**(MCP 서버는 검색 서비스를 직접 import한다 — 이 라우터를 거치지 않는다)
- `/docs/UI_GUIDE.md` — 검색 결과 화면이 무엇을 표시하는지
- `/CLAUDE.md` — "검색은 정형 필터 + 벡터 유사도를 **단일 SQL 쿼리**로 결합한다"
- **이전 step 산출물**:
  - `/backend/app/services/search.py`(step 1) — `search_documents`·`SearchHit`·`SEARCH_SQL`·`MAX_K`. **이 step은 이것을 얇게 감싸기만 한다**
  - `/backend/app/api/deps.py`(step 2) — `get_conn`·`optional_user_id`
  - `/backend/app/api/documents.py`(step 2·3) — 라우터 등록 방식과 응답 모델 스타일
  - `/backend/app/main.py`(step 2에서 수정됨) — lifespan에서 풀을 여는 위치
  - `/backend/tests/conftest.py` — TestClient 픽스처와 문서 생성·임베딩 처리 헬퍼

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

검색 로직은 step 1에서 이미 끝났다. 이 step은 **얇은 라우터**다 — HTTP 계약(요청 검증·응답 모델·상태 코드)만 담당하고, SQL이나 필터 로직을 여기에 만들지 않는다.

MCP 서버(M4)는 이 라우터가 아니라 `services/search.py`를 직접 import한다 (ADR-008). 따라서 **검색 동작을 이 파일에 추가하면 MCP 경로에서는 빠진다.**

## 작업

### 1. `backend/app/main.py`에 임베딩 프로바이더 주입 추가

lifespan에서 `get_provider()`를 **한 번** 호출해 앱 상태에 보관한다.

```python
app.state.provider = get_provider()   # 예시. 위치·이름은 재량
```

**요청마다 `get_provider()`를 부르면 안 된다.** `LocalProvider`는 모델을 인스턴스 속성 `_model`에 lazy-load하므로, 매 요청 새 인스턴스를 만들면 **검색할 때마다 2GB 모델을 다시 로드한다.** 에러 없이 느려지기만 해서 발견하기 어렵다.

생성 자체는 비용이 없다(lazy-load라 lifespan에서 만들어도 기동이 막히지 않는다 — M1에서 확인).

`app/api/deps.py`에 프로바이더를 꺼내는 의존성을 추가하라.

### 2. `backend/app/api/search.py`

#### `POST /api/search`

요청 본문:

```json
{ "query": "임베딩 잡 생성", "tags": ["규정"], "content_type": "pdf", "k": 10 }
```

- `query` — 필수
- `tags`·`content_type` — 선택. 없으면 `null`을 서비스에 그대로 넘긴다(서비스 SQL이 `IS NULL OR ...`로 분기한다)
- `k` — 선택, 기본 10, **`Field(ge=1, le=MAX_K)`**. `MAX_K`는 **`services/search.py`에서 import한다.** 40을 문자열로 적어 넣지 마라 — 서비스의 `EF_SEARCH`가 바뀌면 함께 움직여야 하는 값이다
- `X-User-Id` — **선택**(`optional_user_id`). 없으면 익명이며 public 문서만 검색된다

#### 빈 질의는 400이다 — 조용한 고장을 막는 방어

`query`가 공백 제거 후 비면 **400**을 반환하고 서비스를 호출하지 마라.

근거(실측):

```
'[0,0,0]'::vector <=> '[1,2,3]'::vector  →  NaN     (에러가 아니다)
ORDER BY <0 벡터와의 거리>                →  전 행 NaN, 정렬이 무의미
```

`FakeProvider`는 토큰이 없는 입력에 **0 벡터**를 반환한다. 그러면 모든 거리가 `NaN`이 되어 **에러 없이 무작위 문서 목록**이 반환된다. `avg(embedding)`이 NULL일 때 `embedding <=> NULL`이 정렬을 무의미하게 만드는 것과 **정확히 같은 계열의 고장**이며(CLAUDE.md의 CRITICAL 항목), 사용자에게는 "검색은 됐는데 결과가 이상하다"로 보인다.

#### 응답

```json
{
  "items": [
    { "document_id": "...", "title": "...", "tags": ["규정"], "content_type": "pdf",
      "chunk_index": 3, "content": "...", "score": 0.82 }
  ],
  "sql": "WITH candidates AS (...)"
}
```

- `sql`은 `services/search.py`의 **`SEARCH_SQL` 상수를 그대로** 싣는다. 화면의 "실행된 SQL 보기" 토글이 이것을 표시해, "정형 필터 + 벡터를 단일 SQL로 결합했다"는 주장을 사용자가 직접 확인하게 한다
- **파라미터 값(질의 벡터·`user_id`)을 `sql`에 채워 넣지 마라.** 1024차원 벡터 리터럴이 응답에 실려 수십 KB가 되고, `user_id`가 노출된다. 플레이스홀더가 남은 원본 SQL을 그대로 보낸다
- **`sql` 문자열을 이 파일에서 조립하거나 다시 쓰지 마라.** 서비스의 상수를 참조만 한다 — 두 곳에 존재하는 순간 한쪽이 낡는다

### 3. `backend/tests/test_search_api.py` — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.**

step 1의 **픽스처 작성 규칙이 여기에도 적용된다**:

- `FakeProvider`는 `text.split()` 기반이라 **질의 어절이 문서 본문에 그대로 등장해야** 유사해진다(한국어 조사가 붙으면 다른 토큰이다). 이 성질은 실측 비교 후 유지하기로 결정된 사항이다
- **무관 문서끼리는 유사도가 정확히 0으로 동점이라 정렬이 비결정적이다.** "무관 문서가 N위"를 단언하지 마라
- 순위 테스트 docstring에 **"검증 대상은 API가 서비스 결과를 그대로 전달하는가이지 검색 품질이 아니다"**를 적어라

최소 아래를 확인한다.

1. **정상 검색** — 200이고 `items`에 관련 문서가 들어 있다.
2. **`sql` 필드가 서비스 상수와 동일하다** — `SEARCH_SQL`을 import해 비교한다. 문자열을 테스트에 복사해 적지 마라.
3. **`sql`에 벡터 리터럴이 들어 있지 않다** — 응답 크기가 터지지 않는 것을 고정한다.
4. **빈 질의·공백 질의는 400** — 그리고 **200에 무작위 결과가 오지 않는 것**이 이 테스트의 요점이다.
5. **`k` 검증** — `k=0`과 `k=MAX_K+1`은 422(Pydantic), `k=MAX_K`는 200.
6. **기본 `k`는 10** — `k`를 생략하면 결과가 최대 10건이다.
7. **권한 필터** — 타인의 private 문서가 결과에 없다. **`X-User-Id` 없는 익명 요청은 public만** 받는다. 소유자 본인은 자기 private 문서를 받는다.
8. **태그 필터·유형 필터**가 응답에 반영된다.
9. **필터와 벡터의 결합** — 태그는 맞지만 내용이 무관한 문서보다, 둘 다 맞는 문서가 위에 온다.
10. **문서당 1건** — 같은 문서의 청크가 여러 개 매칭돼도 `items`에 그 문서는 한 번만 나온다.
11. **청크 없는 문서는 안 나온다** — 업로드 직후(워커 미실행).
12. **프로바이더가 재사용된다** — 앱 상태에 보관된 인스턴스가 요청마다 새로 만들어지지 않음을 확인하라. 두 번 검색한 뒤 같은 객체인지 보는 방식으로 충분하다. **이 테스트가 없으면 "매 요청 2GB 재로딩" 회귀를 잡을 수 없다.**

## Acceptance Criteria

```bash
docker compose up -d              # 프로젝트 루트에서
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_search_api.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 라우터에 SQL이나 필터 로직이 없는가 — `services/search.py`를 호출만 하는가?
   - `MAX_K`를 서비스에서 import했는가, 아니면 40을 적어 넣었는가?
   - 프로바이더를 lifespan에서 한 번 만들어 재사용하는가?
   - `sql` 응답이 서비스 상수를 참조하는가 — 이 파일에서 조립하지 않았는가?
   - 빈 질의가 400인가?
3. 결과에 따라 `phases/m2-hybrid-search/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **요청·응답 스키마(필드명과 타입), 프로바이더를 앱 상태에서 꺼내는 의존성 이름을 반드시 포함시켜라.** M3(검색 화면)와 M4(MCP)가 이 계약을 본다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **라우터에 SQL을 쓰지 마라.** 이유: 검색 SQL은 `services/search.py` 한 곳에만 존재해야 한다. MCP 서버(M4)는 이 라우터를 거치지 않으므로, 여기 추가한 로직은 MCP 경로에서 빠진다.
- **요청마다 `get_provider()`를 부르지 마라.** 이유: `LocalProvider`가 매번 2GB 모델을 다시 로드한다. 에러 없이 느려지기만 한다.
- **빈 질의를 그대로 서비스에 넘기지 마라.** 이유: 0 벡터의 코사인 거리가 `NaN`이라 정렬이 무의미해지고, **에러 없이 무작위 문서 목록**이 반환된다(실측 확인).
- **`sql` 응답에 파라미터 값을 채워 넣지 마라.** 이유: 1024차원 벡터 리터럴이 응답을 수십 KB로 부풀리고 `user_id`가 노출된다.
- **`k` 상한을 숫자로 적어 넣지 마라.** 이유: `MAX_K`는 `EF_SEARCH // CANDIDATE_MULTIPLIER`로 유도된 값이다. 숫자를 복사하면 `ef_search`를 바꿀 때 함께 움직이지 않아, 에러 없이 행이 모자라는 상태가 된다.
- **결과를 파이썬에서 다시 정렬하거나 걸러내지 마라.** 이유: 순서와 필터는 SQL이 이미 결정했다. 다시 손대면 `DISTINCT ON` 순서 설계가 무의미해진다.
- **`services/search.py`를 수정하지 마라.** 이유: step 1의 산출물이다. 수정이 필요하다고 판단되면 그 사실을 summary에 적어라.
- **키워드 검색·RRF를 추가하지 마라.** 이유: ADR-016은 조건부 채택이며 M6이다.
- **페이지네이션을 만들지 마라.** 이유: 요청받지 않았다. `k`로 충분하다.
- 기존 테스트를 깨뜨리지 마라.
