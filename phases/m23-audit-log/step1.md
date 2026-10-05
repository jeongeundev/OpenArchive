# Step 1: audit-triggers

쓰기 동작을 **원래 작업과 같은 트랜잭션에서** 감사 로그에 남기는 트리거와, 원본 내려받기를 남기는 DB 함수를 `029_audit_triggers.sql`에 이어 쓴다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 0이 `audit_log` 테이블(028)과 UPDATE·DELETE·TRUNCATE 거부 트리거(029 앞부분)를 만들었다. 칼럼: `id, occurred_at, action, actor, actor_via, db_role(DEFAULT current_user), document_id(FK 없음), document_title, detail jsonb`.
- 행위자는 앱이 트랜잭션 범위 GUC로 넘긴다(앱 쪽 헬퍼는 step 3). 이 step의 트리거는 **읽기만** 한다:
  - `openarchive.actor_id` — 사용자명
  - `openarchive.actor_via` — `session | token | mcp | cli | share | worker`
  - `openarchive.share_id` — 공유 토큰 요청일 때 공유 UUID
- ⚠️ **값이 없으면 NULL이 아니라 빈 문자열이 올 수 있다.** 한 번이라도 설정된 백엔드에서는 placeholder GUC가 `''`로 남는다(HA 실측: 행위자 없는 INSERT 250건 중 241건이 `''`). 반드시 `NULLIF(current_setting('openarchive.actor_id', true), '')`로 읽는다. 안 그러면 행위자 없는 쓰기가 "빈 사용자"로 기록된다.
- 앱 행위자가 없으면 `actor`·`actor_via`는 NULL이고 `db_role`이 `current_user`로 남는다(사용자 결정 — 직접 접속 구분).
- 공유 토큰(`actor_via = 'share'`)일 때는 `detail`에 `share_id`와 `share_name`(`shares.name`)을 넣는다. 공유를 만든 사람을 행위자로 적지 않는다(사용자 결정).
- 사용자 결정: 열람 범위 **값**(public↔private) 변경뿐 아니라 **부여 대상(사용자·그룹) 추가·제거**도 「열람 범위 변경」으로 남긴다. 외부 공유 부여(`document_grants.share_id IS NOT NULL`)는 공유 화면의 별개 축이라 남기지 않는다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-055**(기록 대상 표·결정 2·5·6), ADR-044(부여 테이블·공유), ADR-046(원본 판 `file_version`)
- `backend/openarchive/migrations/028_audit_tables.sql`, `029_audit_triggers.sql` — step 0 산출물
- `backend/openarchive/migrations/002_tables.sql`(documents·document_versions), `003_triggers.sql`(버전 이력 트리거 — v1은 INSERT 때, v2 이상은 `UPDATE OF content_hash` 때 `document_versions`에 INSERT된다), `018_files_tables.sql`(document_files), `025_grants_tables.sql`(groups·group_members·document_grants), `026_shares_tables.sql`(shares, document_grants.share_id)
- `backend/tests/test_audit_log.py` — step 0 테스트(이어서 쓴다)
- `backend/tests/test_triggers.py`, `backend/tests/conftest.py` — 트리거 테스트 헬퍼

## 작업

### 1) 테스트 먼저 — `backend/tests/test_audit_log.py`에 추가

각 테스트는 트랜잭션 안에서 `SELECT set_config('openarchive.actor_id', 'alice', true)`, `set_config('openarchive.actor_via', 'session', true)`로 행위자를 건 뒤 쓰기를 하고, `audit_log`를 읽어 단언한다. **헬퍼로 묶되 내부는 반드시 `set_config(..., true)`를 써라.**

1. **문서 생성** — documents INSERT → `action='document_created'`, `actor='alice'`, `actor_via='session'`, `document_id`·`document_title` 일치.
2. **텍스트 수정** — content·content_hash·version을 바꾸는 UPDATE(v2) → `text_updated`, `detail = {"version": 2}`. **v1 생성에는 `text_updated`가 없다**(생성 1건만).
3. **문서 삭제** — DELETE → `document_deleted`, `document_title`이 삭제된 문서 제목. 그 문서의 이전 기록(생성)도 그대로 남는다.
4. **삭제 시 연쇄 기록 없음** — 사용자·그룹 부여와 원본 판 2개가 있는 문서를 지우면 새로 생기는 감사 행은 **`document_deleted` 정확히 1건**이다(부여 연쇄 삭제가 「부여 제거」로 섞이지 않는다).
5. **열람 범위 값 변경** — `UPDATE documents SET visibility='private'` → `access_changed`, `detail = {"kind":"visibility","before":"public","after":"private"}`. 같은 값으로 UPDATE하면 기록 없음. 제목·태그만 바꾼 UPDATE도 기록 없음.
6. **부여 추가·제거** — 사용자 부여 INSERT → `access_changed`, `detail = {"kind":"grant","change":"added","grantee_type":"user","grantee":"bob"}`. 그룹 부여 DELETE → `change:"removed"`, `grantee_type:"group"`, `grantee`=그룹 이름. **공유 부여(share_id)는 기록 없음.**
7. **부여 대상이 지워질 때 연쇄 기록 없음** — 그룹을 지워 그 그룹의 부여·구성원이 연쇄 삭제돼도 `access_changed`·`group_member_changed`가 생기지 않는다. 사용자를 지울 때도 같다.
8. **그룹 구성원 변경** — group_members INSERT/DELETE → `group_member_changed`, `detail = {"change":"added"|"removed","group":<그룹 이름>,"user":<사용자명>}`, `actor`=수행한 관리자(GUC 값). `document_id`는 NULL. `ON CONFLICT DO NOTHING`으로 아무 행도 안 들어간 INSERT는 기록 없음.
9. **원본 교체** — document_files에 `file_version=2` INSERT → `original_replaced`, `detail={"file_version":2}`. 첫 판(`file_version=1`) INSERT는 기록 없음(생성에 포함).
10. **원본 내려받기** — `SELECT record_original_download(<doc>, 1)` → `original_downloaded`, `detail={"file_version":1}`, 제목 스냅샷.
11. **공유 주체** — `actor_via='share'`, `share_id`=실제 공유 id, `actor_id` 미설정으로 `record_original_download` → `actor` NULL, `detail`에 `share_id`(문자열)·`share_name`.
12. **행위자 없음** — GUC를 한 번도 걸지 않은 연결의 쓰기 → `actor` NULL, `actor_via` NULL, `db_role = current_user`. **같은 연결에서 앞 트랜잭션이 `set_config(..., true)`로 걸었다가 커밋한 뒤**의 쓰기도 `actor` NULL이다(빈 문자열이 아님 — `IS NULL`로 단언).
13. **롤백되면 기록도 없다** — 트랜잭션 안에서 문서 생성 후 ROLLBACK → 감사 행 0.

### 2) 구현 — `backend/openarchive/migrations/029_audit_triggers.sql`에 이어 쓰기

- `audit_record(p_action text, p_document_id uuid, p_title text, p_detail jsonb) RETURNS void` — GUC 셋을 `NULLIF(..., '')`로 읽어 `audit_log`에 INSERT한다. `actor_via = 'share'`이고 공유 id가 있으면 `detail`에 `share_id`·`share_name`을 합친다(`shares`에서 이름 조회, 없으면 이름 없이). 트리거와 `record_original_download`가 공유하는 유일한 INSERT 지점이다.
- 트리거(모두 `AFTER … FOR EACH ROW`):

  | 대상 | 조건 | action | detail |
  |---|---|---|---|
  | `documents` INSERT | — | `document_created` | `{}` |
  | `documents` DELETE | — | `document_deleted` | `{}` (제목은 OLD.title) |
  | `documents` UPDATE OF visibility | `OLD.visibility IS DISTINCT FROM NEW.visibility` (WHEN 절) | `access_changed` | `kind:visibility, before, after` |
  | `document_versions` INSERT | `NEW.version > 1` | `text_updated` | `version` (제목은 documents에서) |
  | `document_files` INSERT | `NEW.file_version > 1` | `original_replaced` | `file_version` |
  | `document_grants` INSERT·DELETE | `share_id IS NULL` | `access_changed` | `kind:grant, change, grantee_type, grantee` |
  | `group_members` INSERT·DELETE | — | `group_member_changed` | `change, group, user` |

- **연쇄 삭제 건너뛰기**: `document_grants` DELETE에서 부모 문서나 부여 대상(사용자·그룹)이 이미 없으면 기록하지 않는다. `group_members` DELETE에서 그룹이나 사용자가 이미 없으면 기록하지 않는다. FK `ON DELETE CASCADE`로 지워지는 자식 행의 트리거가 도는 시점에는 부모 행이 같은 트랜잭션에서 이미 지워져 조회되지 않는다 — 이것을 테스트 4·7로 고정한다.
- `record_original_download(p_document_id uuid, p_file_version int) RETURNS void` — 제목을 documents에서 읽어 `audit_record('original_downloaded', …)`. 앱이 원본 바이트를 읽는 트랜잭션 안에서 부른다(step 3). 문서가 없으면 아무것도 하지 않는다.
- 주석: 왜 트리거인가(경로가 늘어도 빠지지 않는다 — 임베딩 잡과 같은 원칙, ADR-055 맥락), NULLIF 이유(HA 실측), 연쇄 건너뛰기 이유, 공유 부여를 빼는 이유. 함수에 `SECURITY DEFINER`를 쓰지 마라.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit_log.py tests/test_triggers.py tests/test_tables.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(직접 바꿔 보고 되돌린다): ① `NULLIF`를 빼고 `current_setting(...)`을 그대로 쓰기 → 테스트 12 실패 ② `document_grants` DELETE의 부모 존재 확인 제거 → 테스트 4 실패 ③ `group_members` DELETE의 그룹 존재 확인 제거 → 테스트 7 실패 ④ visibility 트리거의 WHEN 절 제거 → 테스트 5 실패 ⑤ `document_versions` 조건 `> 1` 제거 → 테스트 2 실패 ⑥ 공유 부여 제외 조건 제거 → 테스트 6 실패. 하나라도 통과하면 테스트를 보강한다.
3. 전체 스위트에서 기존 테스트가 감사 행 때문에 깨지면 원인을 확인한다. 기존 테스트가 "documents 쓰기 뒤 행 수" 같은 것을 세다 깨지면 감사 테이블을 빼고 세게 고치되 단언을 약하게 만들지 마라.
4. `phases/m23-audit-log/index.json`의 step 1을 갱신한다. summary에 트리거·함수 이름, detail 키 모양, mutant 결과를 적는다.

## 금지사항

- 기존 마이그레이션 파일(001~028)을 고치지 마라. 이유: 이미 적용된 DB에 다시 돌지 않는다. (029는 이 phase에서 만든 파일이라 이어 써도 된다.)
- `current_setting(...)`을 `NULLIF` 없이 쓰지 마라. 이유: 행위자 없는 쓰기가 빈 문자열 사용자로 기록된다(HA 실측).
- 열람(조회)을 기록하는 트리거·함수를 만들지 마라. 원본 내려받기만 예외다. 이유: ADR-055 결정 6.
- 감사 행에 문서 본문·발췌를 넣지 마라. 이유: 관리자에게 보이는 예외는 제목뿐이다(ADR-055 결정 8).
- 서비스·라우터 코드를 고치지 마라. 이유: step 2 이후의 범위다.
- 임시 테이블을 쓰지 마라(CLAUDE.md — OpenProxy에서 다음 클라이언트로 샌다).
- 기존 테스트를 깨뜨리지 마라
