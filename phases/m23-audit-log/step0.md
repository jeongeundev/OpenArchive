# Step 0: audit-table

감사 로그 테이블을 `028_audit_tables.sql`로 만들고, 그 행을 **고칠 수도 지울 수도 없게** 하는 거부 트리거를 `029_audit_triggers.sql`의 앞부분으로 만든다. 기록을 남기는 트리거는 step 1의 범위다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 감사 로그는 **DB가 쓴다**(ADR-055). 쓰기 동작은 대상 테이블의 트리거가, 원본 내려받기는 DB 함수 하나가 원래 작업과 **같은 트랜잭션**에서 기록한다. 앱은 감사 테이블에 직접 INSERT하지 않는다.
- 행위자는 앱이 트랜잭션 범위 GUC로 넘긴다: `openarchive.actor_id`(사용자명), `openarchive.actor_via`(경로), `openarchive.share_id`(공유 토큰일 때). 실측(#186 코멘트, HA OpenProxy transaction 풀)에서 `set_config(..., true)`는 다음 클라이언트로 새지 않았다. 값이 없을 때 `current_setting(name, true)`는 NULL이 아니라 **빈 문자열 `''`**을 돌려줄 수 있다(한 번이라도 설정된 백엔드).
- 사용자 결정(#186 코멘트):
  - 앱 행위자가 없는 쓰기(psql 직접·마이그레이션)는 `actor` NULL, **모든 행에 DB 롤 이름(`current_user`)을 저장**한다.
  - 공유 토큰으로 한 내려받기는 `actor` NULL, 공유 id·이름을 `detail`에 둔다.
  - 감사 테이블의 `TRUNCATE`도 문 단위 트리거로 거부한다.
- 앱 롤은 마이그레이션을 적용하는 **테이블 소유자**라 `REVOKE`는 먹지 않는다. 그래서 거부 수단은 트리거의 예외다(ADR-055 결정 4). 명세서 시험항목: 「DB에 직접 접속해 감사 로그 행을 UPDATE·DELETE하면 거부되고 기록은 그대로 남음」.
- 문서가 지워져도 기록은 남아야 한다 → `document_id`에 **FK를 걸지 않고** 제목을 스냅샷으로 둔다(ADR-055 결정 5). 사용자도 지워질 수 있으므로 `actor`도 사용자명 텍스트 스냅샷이다(`documents.owner_id`와 같은 방식 — 사용자명 text).
- 설치 대상 PostgreSQL은 14 이상이다(OpenSQL은 17.8). 14에서 안 되는 문법을 쓰지 마라.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-055** 전체(「감사 로그는 DB가 같은 트랜잭션에서 남기고…」), ADR-022(임시 테이블·세션 상태 누수)
- `/docs/ARCHITECTURE.md` — 「DB 스키마」
- `backend/openarchive/migrations/025_grants_tables.sql`, `026_shares_tables.sql` — 테이블 파일의 주석 밀도·명명 선례
- `backend/openarchive/migrations/027_edge_jobs_triggers.sql` — 트리거 파일 선례
- `backend/tests/test_tables.py` — 상단 테이블 목록 단언, 헬퍼 함수들
- `backend/tests/conftest.py` — DB 픽스처(실제 pgvector 컨테이너에 마이그레이션 적용)
- `backend/tests/test_migrations.py` — 러너가 파일을 집는 방식

## 작업

### 1) 테스트 먼저 — 새 파일 `backend/tests/test_audit_log.py` (+ `test_tables.py` 목록)

1. `audit_log` 테이블이 있다(`test_tables.py` 상단 테이블 목록 단언에 추가).
2. 행을 직접 INSERT할 수 있고(테스트용 — 앱은 하지 않는다), `db_role`을 생략하면 `current_user`가, `occurred_at`을 생략하면 현재 시각이, `detail`을 생략하면 `'{}'`가 들어간다.
3. `action`이 허용 목록 밖이면 `CheckViolation`. `actor_via`가 허용 목록 밖이면 `CheckViolation`, NULL은 된다.
4. **UPDATE는 거부된다** — 테스트 연결(테이블 소유자 롤)로 `UPDATE audit_log SET actor = 'x'` → 예외(`RaiseException` 계열), 행은 그대로.
5. **DELETE는 거부된다** — 같은 방식. 행 수 그대로.
6. **TRUNCATE는 거부된다** — 행 수 그대로.
7. `document_id`에 존재하지 않는 UUID를 넣어도 된다(FK 없음). 같은 id의 문서를 만들고 지워도 감사 행이 남는다.
8. 거부 예외 메시지가 사람이 읽을 수 있는 한국어 문장이다(예: "감사 로그는 고치거나 지울 수 없습니다.") — 메시지 문자열을 단언한다.

### 2) 구현

**`backend/openarchive/migrations/028_audit_tables.sql`**

```sql
CREATE TABLE audit_log (
  id             bigserial PRIMARY KEY,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  action         text NOT NULL,      -- CHECK: 아래 7개
  actor          text,               -- 앱 행위자 사용자명 스냅샷. 없으면 NULL
  actor_via      text,               -- CHECK: session | token | mcp | cli | share | worker, 또는 NULL
  db_role        text NOT NULL DEFAULT current_user,
  document_id    uuid,               -- FK 없음 (문서 삭제 뒤에도 남는다)
  document_title text,               -- 사건 시점 제목 스냅샷
  detail         jsonb NOT NULL DEFAULT '{}'
);
```

- `action` 허용 값(이름 있는 CHECK `audit_log_action_valid`): `document_created`, `text_updated`, `document_deleted`, `access_changed`, `group_member_changed`, `original_replaced`, `original_downloaded`. 폴더 열람 범위 변경(`folder_access_changed`)은 폴더가 생기는 #187이 CHECK를 넓힌다 — 지금 넣지 마라.
- `actor_via` 이름 있는 CHECK `audit_log_actor_via_valid`.
- 인덱스: `(occurred_at DESC, id DESC)`, `(actor, id DESC)`, `(action, id DESC)` — 관리 화면이 최신순 + 사용자·동작 필터 + `id` 커서로 읽는다.
- 머리 주석: 무엇을·왜(ADR-055 결정 2·4·5), FK를 걸지 않는 이유, `db_role`을 두는 이유(앱 경로와 직접 접속 구분 — pgaudit 관례), 앱이 INSERT하지 않는다는 원칙.

**`backend/openarchive/migrations/029_audit_triggers.sql`** (이 step에서는 거부 트리거만)

- `audit_log_reject_change()` — 예외를 던지는 트리거 함수. 메시지는 테스트 8과 같은 한국어 문장.
- `BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW` 트리거.
- `BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT` 트리거.
- 주석: REVOKE가 아니라 트리거인 이유(앱 롤 = 소유자), 소유자·슈퍼유저가 트리거를 끄는 것은 범위 밖(ADR-055 트레이드오프 1).
- 파일 끝에 "기록 트리거는 아래에 이어진다(step 1)" 같은 표식은 두지 마라 — step 1이 이어서 쓴다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit_log.py tests/test_tables.py tests/test_migrations.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① UPDATE/DELETE 트리거 삭제 → 테스트 4·5 실패 ② TRUNCATE 트리거 삭제 → 테스트 6 실패 ③ `document_id`에 `REFERENCES documents(id) ON DELETE CASCADE` 추가 → 테스트 7 실패 ④ action CHECK 삭제 → 테스트 3 실패. 하나라도 통과하면 테스트를 보강한다.
3. 아키텍처 체크: CLAUDE.md CRITICAL 규칙(마이그레이션은 번호 붙은 raw SQL, 임시 테이블 금지)을 지켰는가.
4. `phases/m23-audit-log/index.json`의 step 0을 갱신한다. summary에 테이블·제약·트리거 이름과 mutant 결과를 적는다.

## 금지사항

- 기존 마이그레이션 파일(001~027)을 고치지 마라. 이유: 이미 적용된 DB(VM·PyPI 사용자)에 다시 돌지 않는다.
- 기록 트리거·내려받기 함수를 만들지 마라. 이유: step 1의 범위다. 이 step에서 만들면 step 1의 테스트 순서(TDD)가 무너진다.
- 거부를 `REVOKE`로 대신하지 마라. 이유: 앱 롤이 테이블 소유자라 효과가 없다(ADR-055 결정 4).
- `document_id`에 FK를 걸지 마라. 이유: 문서 삭제에 기록이 연쇄되면 「문서 삭제」 기록 자체가 사라진다(ADR-055 결정 5).
- 서비스·라우터 코드를 고치지 마라. 이유: step 2 이후의 범위다.
- 기존 테스트를 깨뜨리지 마라
