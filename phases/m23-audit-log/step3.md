# Step 3: actor-rest

행위자를 DB에 넘기는 **헬퍼 하나**(`services/audit.py`의 `set_actor`)를 만들고, REST 인증 의존성에 연결한다. 원본 내려받기는 DB 함수 `record_original_download`를 같은 트랜잭션에서 부르게 한다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 0·1이 `audit_log`와 기록 트리거를 만들었다. 트리거는 트랜잭션 범위 GUC 셋을 읽는다: `openarchive.actor_id`(사용자명), `openarchive.actor_via`(`session | token | mcp | cli | share | worker`), `openarchive.share_id`(공유 UUID). 원본 내려받기는 `SELECT record_original_download(document_id, file_version)`로 남는다.
- **실측(#186 코멘트, HA OpenProxy `transaction` 풀)**:
  - `set_config(name, value, true)`(= `SET LOCAL`)는 COMMIT·ROLLBACK·비정상 종료 어느 경로에서도 다음 클라이언트로 새지 않았다(누수 0/320).
  - **트랜잭션 안에서 `LOCAL` 없이 쓴 `SET`은 COMMIT 뒤에도 백엔드에 남아 다른 클라이언트의 요청에 75/100 붙었다.** 한 글자 실수가 남의 쓰기를 내 이름으로 기록한다. 그래서 행위자 설정은 이 헬퍼 하나로만 하고, 다른 곳에서 GUC를 직접 쓰지 못하게 아키텍처 테스트로 막는다.
- **REST의 트랜잭션 구조**: `api/deps.py`의 `get_conn`이 요청 하나에 풀 연결 하나를 빌려주고, 연결은 autocommit이 아니어서 요청 전체가 **암묵 트랜잭션 하나**다. 커밋은 응답 직전에 일어난다(`scope="function"`). 같은 요청의 의존성들은 같은 연결을 공유한다(FastAPI 의존성 캐시). 그래서 `current_user`에서 인증이 끝난 직후 한 번 걸면 그 요청의 모든 쓰기 트리거가 같은 행위자를 본다. 서비스 안의 `conn.transaction()`은 SAVEPOINT가 되며 `SET LOCAL` 값을 지우지 않는다(롤백된 SAVEPOINT 안에서 건 값만 되돌려진다).
- **autocommit 연결 주의**: 트랜잭션 밖에서 `set_config(..., true)`를 부르면 그 문장이 끝나는 순간 값이 사라져 아무 효과가 없다(오류도 없다). 운영자 CLI·워커는 autocommit 연결을 쓴다(step 5·6). 헬퍼는 이 경우를 조용히 넘기지 말고 거부한다.
- 행위자 결정: 세션 → 로그인 사용자(`via='session'`), 사용자 토큰 → 토큰 주인(`via='token'`), 공유 토큰 → `actor` 없음 + `via='share'` + `share_id`.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-055**(결정 3: 행위자는 `SET LOCAL`로만), ADR-022, ADR-034(토큰), ADR-044 「공유」
- `backend/openarchive/api/deps.py` — `get_conn`, `current_user`, `require_*`
- `backend/openarchive/services/auth.py` — `validate_session`·`validate_token`이 돌려주는 dict(`kind`, `credential`, `principal`, `username`, `share_id`)와 상수 `CREDENTIAL_SESSION`·`CREDENTIAL_TOKEN`·`PRINCIPAL_SHARE`
- `backend/openarchive/services/documents.py` — `get_original_file`(811행 근처)
- `backend/openarchive/api/documents.py` — 원본 내려받기 라우트
- `backend/openarchive/migrations/029_audit_triggers.sql` — step 1의 `audit_record`·`record_original_download`
- `backend/tests/test_architecture.py` — 정적 규칙 테스트 선례
- `backend/tests/test_audit_log.py`, `backend/tests/test_documents_api.py`, `backend/tests/test_token_access.py`, `backend/tests/test_share_access.py`, `backend/tests/test_groups_api.py` — API 테스트 헬퍼(로그인·토큰 발급·공유 토큰)

## 작업

### 1) 테스트 먼저

**새 파일 `backend/tests/test_audit.py`** — 서비스·REST 경로가 행위자를 남기는지(명세서 시험항목의 기록 대상):

1. `set_actor(conn, actor="alice", via="session")` 뒤 같은 트랜잭션에서 문서 생성 → 감사 행 `actor='alice'`, `actor_via='session'`.
2. **같은 연결의 다음 트랜잭션**(커밋 뒤)에서 행위자 없이 쓰면 `actor IS NULL` — 헬퍼가 `is_local=true`를 쓴다는 증거.
3. **앱 풀에서 연결을 두 번 빌려** 첫 번째에서 `set_actor` 후 커밋·반납, 두 번째에서 행위자 없이 쓰면 `actor IS NULL`.
4. `via`가 허용 목록 밖이면 `ValueError`. `via='share'`인데 `share_id`가 없으면 `ValueError`.
5. autocommit 연결에서 트랜잭션 밖에서 부르면 `RuntimeError`(조용히 무시하지 않는다). 같은 연결의 `async with conn.transaction():` 안에서는 된다.
6. REST: 세션으로 텍스트 문서 생성 → `document_created`, `actor`=로그인 사용자, `via='session'`. 이어서 텍스트 편집 → `text_updated` `{"version": 2}` 같은 사용자.
7. REST: **API 토큰(read_write)으로 문서 생성 → `actor`=토큰 주인, `via='token'`**(명세서: 「API 토큰으로 REST나 원격 MCP를 통해 만든 문서도 토큰 주인의 이름으로 「문서 생성」이 기록됨」).
8. REST: 열람 범위를 조직 공개 → 제한으로 바꾸면 `access_changed` `kind:visibility`, `before:"public"`, `after:"private"`, 행위자=소유자.
9. REST: 관리자가 그룹에 사용자를 넣고 빼면 `group_member_changed` 2행, `actor`=관리자, `detail.group`·`detail.user`.
10. REST: 원본 파일 업로드 → 교체 → 내려받기 → `original_replaced` `{"file_version": 2}`, `original_downloaded` `{"file_version": 2}`(최신 판), `file_version=1`을 지정해 받으면 `{"file_version": 1}`.
11. REST: 공유 토큰으로 원본 내려받기 → `actor IS NULL`, `via='share'`, `detail.share_name`=공유 이름.
12. REST: 문서 삭제 → `document_deleted`, 제목 스냅샷, 이전 기록이 남는다.
13. REST: **볼 수 없는 문서의 원본 요청(404)은 내려받기 기록을 남기지 않는다.**

**`backend/tests/test_architecture.py`에 추가** — 정적 규칙:

14. `backend/openarchive/` 아래(마이그레이션 SQL 제외) 어떤 파일에도 `INSERT INTO audit_log`가 없다.
15. `openarchive.actor_id`·`openarchive.actor_via`·`openarchive.share_id` 문자열은 `services/audit.py`와 마이그레이션 SQL에만 나온다.
16. 앱 코드에 `SET openarchive.`(세션 SET)가 없다.

### 2) 구현

**새 파일 `backend/openarchive/services/audit.py`**

```python
ACTOR_VIA: tuple[str, ...] = ("session", "token", "mcp", "cli", "share", "worker")

async def set_actor(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None,
    via: str,
    share_id: UUID | None = None,
) -> None:
    """이 트랜잭션의 감사 행위자를 DB에 넘긴다 (ADR-055 결정 3). ..."""
```

- 세 GUC를 `SELECT set_config(%s, %s, true)`로만 쓴다. 값이 없으면 빈 문자열을 넣는다(트리거가 `NULLIF`로 읽는다) — 같은 트랜잭션에서 두 번 부르면 앞 값을 확실히 덮어쓰게.
- `via` 검증, `share` ↔ `share_id` 짝 검증.
- `conn.autocommit`이고 트랜잭션 밖(`conn.info.transaction_status`가 IDLE)이면 `RuntimeError` — 메시지에 "트랜잭션 안에서 불러야 한다"를 적는다.
- docstring에 HA 실측 근거(세션 `SET` 75/100 누수)를 한 줄로 남긴다.

**`backend/openarchive/api/deps.py` — `current_user`**: 인증이 성공한 경우에만 `set_actor`를 부른다. 세션 → `actor=username, via="session"`, 사용자 토큰 → `via="token"`, 공유 토큰 → `actor=None, via="share", share_id=…`. 익명이면 부르지 않는다.

**`backend/openarchive/services/documents.py` — `get_original_file`**: 판을 고를 때 `file_version`도 읽고, 반환 직전에 `SELECT record_original_download(%s, %s)`를 같은 연결로 부른다. 열람 검증(`ensure_visible`)과 판 존재 확인을 통과한 뒤에만 부른다. 반환 dict의 기존 키는 유지한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit.py tests/test_architecture.py tests/test_deps.py tests/test_documents_api.py tests/test_token_access.py tests/test_share_access.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① `set_config(..., true)`를 `false`로 → 테스트 2·3 실패 ② autocommit 거부 제거 → 테스트 5 실패 ③ `current_user`의 토큰 분기에서 `via="session"`으로 잘못 넘기기 → 테스트 7 실패 ④ `record_original_download` 호출을 `ensure_visible` 앞으로 옮기기 → 테스트 13 실패. 하나라도 통과하면 테스트를 보강한다.
3. 아키텍처 체크: 앱이 `audit_log`에 INSERT하지 않는가, GUC를 헬퍼 밖에서 쓰지 않는가(테스트 14~16).
4. `phases/m23-audit-log/index.json`의 step 3을 갱신한다. summary에 `set_actor` 시그니처와 연결 지점을 적는다.

## 금지사항

- `SET openarchive.…`이나 `set_config(..., false)`를 쓰지 마라. 이유: HA 실측에서 세션 범위 값이 다른 클라이언트의 요청에 75/100 붙었다 — 감사 기록 위조다.
- 앱에서 `audit_log`에 INSERT하지 마라. 이유: 감사 로그는 DB가 쓴다(ADR-055 결정 2).
- 라우터마다 `set_actor`를 부르지 마라 — `current_user` 한 곳에서만. 이유: 경로가 늘 때마다 빠질 자리가 생긴다.
- 열람·검색·목록 조회를 기록하지 마라. 이유: ADR-055 결정 6.
- MCP·CLI·워커 코드를 고치지 마라. 이유: step 4~6의 범위다.
- 기존 테스트를 깨뜨리지 마라
