# Step 0: subject-visibility

## 배경 — 계약의 일부를 인터페이스가 대신 지키고 있다

`find_related`·`suggest_tags`·`resolve_links`·`find_backlinks` 네 함수는 **결과 문서**의 열람
범위만 거르고 **주체 문서**(요청 대상 문서 자체)는 거르지 않는다. 지금 안전한 이유는 서비스가
아니라 인터페이스가 막아주기 때문이다.

- REST — 라우터가 결과를 버리는 선행 `get_document` 호출로 방어한다
  (`backend/app/api/documents.py:106, 119, 133, 145`)
- MCP — `get_document`를 먼저 부르는 호출 순서 덕에 **우연히** 안전하다
  (`backend/mcp_server/server.py:109-116`)

PRD §4의 경계 규칙은 *"인터페이스는 사용성에서 서로 달라도 되지만, **계약을 약화할 수 없다**"*
이다. 지금 구조는 그 반대다 — 인터페이스가 협조해야 계약이 성립한다. 서비스를 직접 부르는 새
인터페이스(M11-b 텍스트 API, M11-d MCP 쓰기 도구)가 이 호출 순서를 잊으면, 비공개 문서의
`based_on_version`·`reason`(색인 상태)·`identical` 목록이 그대로 새어 나간다.

특히 `find_related`는 `backend/app/services/related.py:116-131`에서 청크가 0건이면 `identical`을
**채운 채로** 조기 반환한다. 즉 주체가 비가시 문서여도 "이 문서와 내용이 같은 문서 목록"이
산출된다. `suggest_tags`는 `related.py:174-177`에서 열람 범위 없이 주체의 태그를 직접 읽는다.

### ✅ 결정 1 — 검증 실패는 `DocumentNotFound` 예외다

**이 결정은 이미 닫혔다. 다시 판단하지 말고, 물어보지도 말고, 아래대로 진행하라.**

- 지금 REST의 관측 동작이 **404**다(라우터 가드 → `DocumentNotFound` → `main.py:49-51` 전역
  핸들러). 조용한 빈 결과로 바꾸면 `200 []`가 되어 **회귀**다.
- `backend/app/services/documents.py:25`의 `DocumentNotFound` docstring이 이미 *"문서가 없거나,
  볼 권한이 없어 존재를 알려주지 않는 경우"*를 규정한다. 새 예외를 만들지 마라.
- MCP도 같은 예외를 그대로 받는다 (`server.py`에는 HTTP 핸들러가 없어 예외가 전파된다 —
  현재 `get_document` 도구의 동작과 동일하므로 변화 없음).

### ✅ 결정 2 — 라우터의 선행 `get_document` 가드 4개를 제거한다

**이 결정은 이미 닫혔다.** 사용자가 명시적으로 승인했다.

- 서비스가 스스로 검증하면 이 가드는 중복이다. 남겨두면 "누가 계약을 지키는가"가 두 곳이 되어
  이번 단계의 취지가 사라진다.
- 비용도 크다. `get_document`는 `documents.py:147-173`에서 **본문 전문 + `document_versions`
  전체**를 읽는데, 가드는 그 반환값을 버린다. 존재 확인에 문서 하나를 통째로 로드하는 셈이다.
- 제거해도 응답은 같아야 한다 — 그것을 `tests/test_related_api.py`·`tests/test_documents_api.py`의
  기존 404 단언이 보증한다. **그 테스트를 고치지 마라.** 고쳐야 한다면 구현이 틀린 것이다.

### 🔴 순환 import 주의

`DocumentNotFound`는 `app/services/documents.py`에 있고, `app/services/visibility.py`는 SQL
조각 하나만 든 4줄 파일이다. **검증 헬퍼를 `visibility.py`에 두면 `visibility.py → documents.py`
import가 생기고, `documents.py:15`가 이미 `visibility.py`를 import하므로 순환이 된다.**
헬퍼는 `documents.py`에 둔다 — 예외와 같은 모듈이고, `related.py`·`links.py`는 현재
`documents.py`를 import하지 않으므로 새 방향의 의존만 생긴다.

## 읽어야 할 파일

- `backend/app/services/related.py` — 고칠 대상. `RELATED_SQL`(:15)·`IDENTICAL_SQL`(:25)·
  `TAG_SUGGESTION_SQL`(:35)이 어디에 열람 술어를 걸고 있는지, `_get_chunk_state`(:90)와
  `find_related`(:100)·`suggest_tags`(:154)의 트랜잭션 구조
- `backend/app/services/links.py` — 고칠 대상. `resolve_links`(:43)·`find_backlinks`(:59)
- `backend/app/services/documents.py` — 헬퍼를 추가할 곳. 예외 5종(:25-49), `get_document`(:143),
  `_load_for_write`(:52)의 판정 방식, 모듈 docstring이 밝힌 "서비스는 HTTP를 모른다" 원칙
- `backend/app/services/visibility.py` — `VISIBLE_TO_USER`는 별칭 `d`와 바인딩명 `%(user)s`에
  고정되어 있다. 주체를 검증하려면 주체 테이블을 `d`로 별칭해야 한다
- `backend/app/api/documents.py:96-150` — 제거할 가드 4곳
- `backend/tests/test_visibility.py` — 테스트를 추가할 곳. `visible_documents` fixture(:30 근처)와
  3자 호출 관례(`anonymous`/`other_user`/`owner`, `user_id=None`/`"bob"`/`"alice"`)
- `docs/ADR.md` ADR-027 — 관계 그래프의 권한 규칙. **단, 같은 ADR의 "경계를 거는 자리는 HTTP
  계층이다"(`docs/ADR.md:1091`)는 이 step이 뒤집는 대상이다. ADR 본문 수정은 step 6에서 한다 —
  이 step에서는 문서를 고치지 마라**

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_visibility.py`에 3자(익명·타인·소유자) 행동 테스트를 추가한다. **서비스
함수를 직접 호출한다 — 라우터를 거치지 마라.** 이 파일의 기존 테스트가 전부 그 방식이다.

기존 `visible_documents` fixture는 public(alice)·alice-private·bob-private 세 문서를 준다.
이번 테스트의 주체는 **`bob_private_id`**다(= alice와 익명이 볼 수 없는 문서).

네 함수 각각에 대해 다음을 단언한다:

```python
# 익명과 타인은 주체 문서의 존재 자체를 알 수 없다
with pytest.raises(DocumentNotFound):
    await find_related(visibility_conn, document_id=bob_private_id, user_id=None)
with pytest.raises(DocumentNotFound):
    await find_related(visibility_conn, document_id=bob_private_id, user_id="alice")

# 소유자는 정상 결과를 받는다
owner = await find_related(visibility_conn, document_id=bob_private_id, user_id="bob")
assert owner.based_on_version is not None      # 색인 상태가 실제로 보인다
```

추가로 **존재하지 않는 문서 ID**(`uuid4()`)에 대해 소유자 자격으로 불러도 `DocumentNotFound`가
나는지 단언한다. 없는 문서와 볼 수 없는 문서가 구별되지 않아야 한다 (ADR-027).

테스트 이름은 이 파일의 관례대로 문장형으로 쓴다. 예:
`test_related_hides_the_existence_of_an_invisible_subject_document`.

`suggest_tags`·`resolve_links`·`find_backlinks`도 같은 방식으로 각각 단언한다. 네 함수를 한
테스트에 몰아넣지 말고 함수별로 나눠라 — 어느 함수가 새는지 실패 메시지에서 바로 보여야 한다.

**이 시점에 테스트를 실행해 실패를 확인하라.** 지금은 익명·타인 호출이 예외 없이 빈 결과를
돌려주므로 `DID NOT RAISE`로 떨어져야 한다. 실패하지 않으면 테스트가 잘못된 것이다.

### 2) `documents.py`에 검증 헬퍼를 추가한다

```python
async def ensure_visible(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str | None = None
) -> None:
    """주체 문서를 볼 수 없으면 DocumentNotFound를 던진다."""
```

- SQL은 모듈 레벨 UPPER_SNAKE 상수로 둔다 (이 저장소의 서비스 관례 — `RELATED_SQL`,
  `BACKLINKS_SQL` 등). 이름은 `SUBJECT_VISIBLE_SQL`.
- **본문을 읽지 마라.** `SELECT 1 FROM documents d WHERE d.id = %(id)s AND {VISIBLE_TO_USER}`로
  존재 여부만 본다. 라우터 가드를 제거하는 이유의 절반이 이 비용이다.
- 반환값 없음. 통과하면 조용히 끝나고, 실패하면 `DocumentNotFound`를 던진다.
- **주석에 반드시 남길 것**: 이 함수가 존재하는 이유 — *"인터페이스가 아니라 서비스가 주체
  문서의 열람 범위를 검증한다. 서비스를 직접 부르는 인터페이스가 늘어도 계약이 약해지지 않게
  하기 위함이다"*.

### 3) 네 함수에 검증을 건다

`related.py`의 `find_related`·`suggest_tags`, `links.py`의 `resolve_links`·`find_backlinks`가
각각 **자기 트랜잭션 안 첫 쿼리로** `ensure_visible`을 호출한다.

- `find_related`·`suggest_tags`는 이미 `async with conn.transaction():` 블록이 있다
  (`related.py:116`, `:167`). 그 블록 **안** 맨 앞에 넣어라. 밖에 두면 검증과 조회가 다른
  스냅샷을 보게 된다.
- `resolve_links`·`find_backlinks`에는 트랜잭션 블록이 없다. **새로 만들지 마라** — 검증 쿼리와
  본 쿼리를 순서대로 실행하면 된다. 이 두 함수는 단일 SELECT이고, 그 사이에 문서의 visibility가
  바뀌는 경합은 이 제품의 보장 범위 밖이다(권한은 조회 시점 규칙 — ADR-027).
- `_get_chunk_state`(`related.py:90`)에 조인을 끼워 넣어 겸용하지 마라. 두 관심사(주체 검증 /
  청크 상태)가 한 쿼리에 섞이면 `links.py`가 재사용할 수 없고, 반환 튜플의 의미도 흐려진다.

`suggest_tags`가 열람 범위 없이 주체 태그를 읽던 `related.py:174-177`은 `ensure_visible`이
앞에서 막으므로 그대로 두어도 된다. **이중으로 술어를 걸지 마라** — 같은 판정을 두 곳에 두면
나중에 한쪽만 바뀐다.

### 4) 라우터 가드 4개를 제거한다

`backend/app/api/documents.py`의 `:106`·`:119`·`:133`·`:145` 네 줄
(`await service.get_document(conn, document_id, user_id=user_id)`)을 지운다.

> 🔴 **`:96`은 지우지 마라.** 겉모습이 같지만 그 줄은 `document = await service.get_document(...)`로
> **반환값을 응답에 쓴다**(문서 상세 엔드포인트). 지울 대상은 할당 없이 호출만 하는 네 줄이다.

제거로 `service` import가 고아가 되는지 확인하라 — 같은 모듈의 다른 라우터가 `service`를 계속
쓰면 그대로 둔다. **쓰이지 않게 된 import만** 정리한다.

MCP(`backend/mcp_server/server.py:109-116`)는 **건드리지 마라.** 거기서는 `get_document`의
반환값을 실제로 응답에 쓴다. 중복 호출이 아니다.

## Acceptance Criteria

```bash
cd backend

# 1) 새 3자 테스트가 통과한다
.venv/bin/pytest tests/test_visibility.py -q
#   → 전부 passed. 실패 0

# 2) 라우터 가드를 지웠는데도 REST 404 계약이 그대로다
.venv/bin/pytest tests/test_related_api.py tests/test_documents_api.py -q
#   → 전부 passed. 404를 단언하는 기존 테스트가 통과해야 한다

# 3) 서비스·MCP 회귀 없음
.venv/bin/pytest tests/test_related.py tests/test_links.py tests/test_mcp_server.py -q
#   → 전부 passed

# 4) 결과를 버리는 가드만 사라졌는지 센다
#    (:96의 `document = await service.get_document(...)`는 반환값을 쓰므로 남아야 한다.
#     그래서 "할당 없이 호출만 하는 줄"을 센다)
grep -cE "^\s+await service\.get_document" app/api/documents.py || echo "0건 — 가드 제거 완료"
#   → "0건 — 가드 제거 완료"

grep -c "document = await service.get_document" app/api/documents.py
#   → 1 (문서 상세 엔드포인트는 그대로 남아야 한다)

# 5) 검증이 서비스에 실제로 들어갔는지 센다
grep -c "ensure_visible" app/services/related.py app/services/links.py
#   → related.py:2  links.py:2  (import 1 + 호출 1씩이면 2, import 방식에 따라 3 이상도 정상.
#      0이면 실패다)

# 6) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다. backend pytest 전량 통과
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다. **테스트를 고쳐서 통과시키지 마라** — 기존 API 테스트가
   깨지면 구현이 틀린 것이다.
2. 아키텍처 체크리스트를 확인한다:
   - 서비스 계층에 fastapi/starlette import가 들어가지 않았는가? (계층 경계 — ARCHITECTURE.md)
   - SQL이 모듈 레벨 상수로 한 곳에만 있는가?
   - 새 예외 타입을 만들지 않았는가? (`DocumentNotFound` 재사용)
   - `VISIBLE_TO_USER`를 복사해 다시 쓰지 않고 `visibility.py`의 것을 import했는가?
3. `docs/` 아래 문서는 **이 step에서 고치지 않는다.** ARCHITECTURE.md 「세 가지 공통 규칙」의
   *"대상 문서 자체의 접근 권한은 API 레이어의 문서 조회에서 이미 걸린다"*(`:672`)가 이 변경으로
   낡지만, 문서 정합은 step 6에서 한 번에 처리한다.
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`ensure_visible`을 `visibility.py`에 두지 마라.** 이유: `documents.py`가 이미 `visibility.py`를
  import하므로 순환 import가 된다.
- **실패를 빈 결과로 표현하지 마라.** 이유: REST가 지금 404를 내고 있어 `200 []`는 회귀다.
- **기존 API 테스트의 404 단언을 고치지 마라.** 이유: 그 단언이 이번 리팩터링의 회귀 검출기다.
  통과하지 않으면 구현을 고쳐야 한다.
- **MCP `server.py`의 `get_document` → `find_related` 순서를 건드리지 마라.** 이유: 거기서는
  반환값을 응답에 쓰므로 중복 호출이 아니다.
- **`ensure_visible`에서 본문(`content`)을 SELECT 하지 마라.** 이유: 존재 확인에 문서를 통째로
  읽는 비용이 라우터 가드를 제거하는 이유의 절반이다.
- **새 엔드포인트·응답 필드를 만들지 마라.** 이유: 이 단계는 계약을 봉인하는 것이지 표면을
  넓히는 것이 아니다. 표면 확장은 M11-b~d의 몫이다.
- **`docs/` 문서를 고치지 마라.** 이유: 문서 정합은 step 6에서 한 번에 한다. 여기서 부분적으로
  고치면 step 6과 충돌한다.
- **기존 테스트를 깨뜨리지 마라.**
