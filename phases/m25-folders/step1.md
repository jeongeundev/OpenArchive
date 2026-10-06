# Step 1: folder-audit

폴더 열람 범위 변경과 문서의 「폴더 범위 따름 / 개별 지정」 전환·폴더 이동을 **DB 트리거**가 감사 로그에 남기게 한다. 새 마이그레이션 `031_folder_audit_triggers.sql`.

## 공통 배경 — m25-folders 설계 결정 (모든 step 같음)

이슈 #187의 폴더 백엔드다(목록 찾기는 m24-doc-finder, 화면은 다음 phase, CLI `import --keep-folders --grant-group`은 그다음 phase). 근거 ADR-054, 스파이크 `notes/folder-acl-spike-20261005.md`(로컬 전용 — 없으면 아래 요약으로 충분하다). 2026-10-06 사용자 결정:

- **D1 범위는 최상위 폴더만 갖는다.** 최상위 폴더는 「조직 공개(public)」 또는 「제한(private) + 사용자·그룹 부여」. 하위 폴더는 항상 「상위 폴더 범위 따름」 — 자기 범위가 없다. 새 최상위 폴더 기본은 조직 공개.
- **D2 폴더 이동(폴더를 다른 폴더 아래로)은 없다.** 문서의 폴더 이동만 있다. 그래서 폴더의 최상위 조상은 만든 뒤 바뀌지 않는다.
- **D3 폴더를 볼 수 있는 사용자는 그 안에 하위 폴더를 만들고 자기 문서를 넣을 수 있다.** 넣은 문서는 기본 「폴더 범위 따름」이다.
- **D4 폴더를 만든 계정은 삭제를 거부한다** — 문서를 소유한 계정 삭제 거부(`services/auth.py` `delete_user`)와 같은 방식. 권한 이전 기능은 없다.
- **D5 볼 수 없는 폴더 안의 문서가 「개별 지정」으로 보이면** 문서는 목록·검색에 나오되 폴더 정보(id·이름·경로)는 어디에도 나오지 않는다. 🔒 같은 자리 표시도 없다(CLAUDE.md: 표시 자체가 존재를 누출한다).
- **D6 폴더 문서 수와 폴더별 목록은 그 폴더에 직접 든 문서만** 센다(하위 폴더 제외). 검색 폴더 필터만 하위 폴더를 포함한다.
- **D7 벡터 검색에 `SET LOCAL hnsw.iterative_scan = strict_order`를 건다.** 스파이크: 열람 범위가 좁은 사용자(문서 5.9%)의 recall@10 0.40 → 1.00, 폴더 필터 0.27 → 0.99. ADR-011 보강 3의 "켜지 않는다"를 뒤집는 결정이다.
- **D8 경계**: 폴더 열람 범위 변경은 **세션 전용**(`require_session_user`), 관리자도 불가 — 폴더를 **만든 사람만**. 폴더 만들기·이름 변경·삭제·문서 폴더 이동은 문서 쓰기와 같은 경계(`require_write_user_id`, 토큰 허용). 이름 변경·삭제는 만든 사람 또는 관리자(볼 수 있는 폴더에 한함). 문서 폴더 이동은 문서 소유자만. MCP는 이번에 바꾸지 않는다.
- 그 밖의 ADR-054 결정: 폴더 범위 변경·그룹 구성원 변경은 **조회 시점 판정**으로 즉시 반영(실효 범위를 저장하지 않는다). 폴더를 만든 사람은 자기 제한 폴더 안의 남의 문서(「폴더 범위 따름」)도 본다. 「개별 지정」 문서는 폴더 범위와 무관하게 문서 자신의 `visibility`·부여로 판정하며, 폴더를 만든 사람에게도 그 판정대로다. 문서 소유자는 언제나 자기 문서를 본다. 외부 공유 주체(`share:<uuid>`)는 폴더를 보지 못하고, 공유에 부여된 문서만 본다(지금과 같음). 삭제는 빈 폴더만("폴더가 비어 있지 않습니다.") — 하위 폴더가 있어도 비어 있지 않다.
- 술어 형태(스파이크 A1): 문서의 폴더에서 **조상으로 거슬러 올라가는 상관 재귀 `EXISTS (WITH RECURSIVE …)`**. 비상관 `IN (서브쿼리)`는 금지 — 3천 청크에서 HNSW를 버리고 generic plan에서 15배 느려졌다. `db.py`의 `prepare_threshold=None`은 유지한다.

### 스키마 (step 0이 만든다 — `030_folders_tables.sql`)

- `folders(id uuid PK, parent_id uuid NULL → folders ON DELETE RESTRICT, name text, created_by text(사용자명, documents.owner_id와 같은 방식), visibility text NULL, created_at, updated_at)`
  - CHECK: 이름은 공백뿐이 아니고 `/`를 포함하지 않는다. `visibility IN ('public','private')`. **최상위만 범위를 갖는다**: `(parent_id IS NULL) = (visibility IS NOT NULL)`.
  - 같은 부모 아래 이름 유일(하위 폴더만). **최상위 이름은 유일하게 하지 않는다** — 남의 제한 폴더 이름과 충돌한다는 오류가 그 폴더의 존재를 누출한다.
- `folder_grants(folder_id → folders ON DELETE CASCADE, user_id → users CASCADE, group_id → groups CASCADE, created_at)` — 대상 정확히 하나(CHECK), 대상별 부분 유니크 인덱스. 외부 공유 부여는 없다.
- `documents.folder_id uuid NULL → folders ON DELETE RESTRICT`, `documents.follows_folder boolean NOT NULL DEFAULT true`. 판정: `folder_id IS NOT NULL AND follows_folder`이면 폴더(최상위) 범위, 아니면 문서 자신의 `visibility`·`document_grants`.

## 감사 로그 현황 (#186, 028·029)

- `audit_log(action, actor, actor_via, db_role, document_id, document_title, detail jsonb, …)`. INSERT 지점은 DB 함수 `audit_record(p_action, p_document_id, p_title, p_detail)` 하나 — 행위자는 `NULLIF(current_setting('openarchive.actor_id', true), '')` 등으로 읽는다. 앱은 `audit_log`에 INSERT하지 않는다(CLAUDE.md CRITICAL).
- CHECK `audit_log_action_valid`(028): `document_created, text_updated, document_deleted, access_changed, group_member_changed, original_replaced, original_downloaded`.
- 문서 열람 범위: `trg_audit_visibility_changed`(`AFTER UPDATE OF visibility … WHEN (OLD IS DISTINCT FROM NEW)`) → `access_changed {kind:'visibility', before, after}`. 부여: `audit_grant_changed`(`document_grants` INSERT/DELETE) → `{kind:'grant', change, grantee_type, grantee}`, 공유 부여·CASCADE 삭제는 건너뛴다.
- 앱 쪽 목록: `services/audit.py`의 `AUDIT_ACTIONS`. `backend/tests/test_audit_log.py`의 `test_service_lists_match_db_constraints`(44행)가 DB CHECK와 이 목록이 같은지 검사하고, `test_allowed_actions`(30행)에 목록이 하드코딩돼 있으며, **`test_invalid_values_are_rejected`(61행 근처)는 `'folder_access_changed'`가 거부되는지 단언한다** — 이 step에서 그 값이 허용되므로 거부 예시를 다른 값으로 바꾼다(거부 검사 자체는 유지).
- 명세서 시험항목: 「폴더의 열람 범위를 바꾸면 「폴더 열람 범위 변경」 기록에 폴더 이름과 이전·이후 값이 남음」.

## 읽어야 할 파일

- `backend/openarchive/migrations/028_audit_tables.sql`, `029_audit_triggers.sql` — 전부
- `backend/openarchive/migrations/030_folders_tables.sql` — step 0
- `backend/openarchive/services/audit.py`, `backend/openarchive/api/audit.py`
- `backend/tests/test_audit_log.py` — 헬퍼·선례
- `/docs/ADR.md` — ADR-055(감사 로그), ADR-054 결정 8

## 작업

### 1) 테스트 먼저 — `backend/tests/test_audit_log.py`에 추가(SQL로 직접 쓰고 `audit_log`를 읽는다)

1. 최상위 폴더 `visibility` public → private: `action='folder_access_changed'`, `document_id NULL`, `detail = {kind:'visibility', folder_id, folder_name, before:'public', after:'private'}`. 같은 값 UPDATE는 기록 없음.
2. `folder_grants`에 그룹 「사업팀」 INSERT / DELETE → `folder_access_changed {kind:'grant', change:'added'|'removed', grantee_type:'group', grantee:'사업팀', folder_id, folder_name}`.
3. 빈 폴더를 지워 `folder_grants`가 CASCADE로 지워질 때는 기록하지 않는다(029의 CASCADE 건너뛰기와 같은 원칙). 사용자·그룹 삭제로 CASCADE될 때도 마찬가지.
4. 문서 `follows_folder` true → false: `access_changed {kind:'inherit', before:'folder', after:'own'}`(되돌리면 반대). 문서 제목 스냅샷이 남는다.
5. 「폴더 범위 따름」 문서의 `folder_id` 변경: `access_changed {kind:'folder', before:<이전 폴더 이름 또는 null>, after:<새 폴더 이름 또는 null>}`. `follows_folder = false`인 문서의 폴더 이동은 열람이 바뀌지 않으므로 기록하지 않는다.
6. 행위자: `set_config('openarchive.actor_id','kim',true)` 아래에서의 변경은 `actor='kim'`.
7. `audit_log_action_valid`가 `folder_access_changed`를 허용하고, `test_service_lists_match_db_constraints`가 통과한다.

### 2) 구현

- `031_folder_audit_triggers.sql`:
  - `audit_log_action_valid`를 DROP 후 `folder_access_changed`를 더해 다시 만든다(같은 트랜잭션).
  - 폴더 트리거 함수·트리거(`folders` `AFTER UPDATE OF visibility … WHEN DISTINCT`, `folder_grants` `AFTER INSERT OR DELETE`), 문서 트리거(`AFTER UPDATE OF follows_folder, folder_id ON documents`) — 기록은 전부 `audit_record`를 거친다.
  - 폴더 기록의 `document_id`·`document_title`은 NULL, 폴더 이름은 `detail.folder_name` 스냅샷.
- `services/audit.py`의 `AUDIT_ACTIONS`에 `folder_access_changed`를 더한다(DB CHECK와 **함께**).
- 프런트 라벨(`types.ts` `AuditAction`, 감사 화면 `ACTION_LABEL`)은 이 phase에서 고치지 않는다 — 다음 phase(화면)의 범위다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit_log.py tests/test_triggers.py tests/test_tables.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 폴더 visibility 트리거의 `WHEN (… IS DISTINCT FROM …)`을 빼면 테스트 1(같은 값 기록 없음)이, CASCADE 건너뛰기를 빼면 테스트 3이 실패해야 한다.
3. `phases/m25-folders/index.json`의 step 1을 갱신한다.

## 금지사항

- 앱 코드에서 `audit_log`에 INSERT하지 마라. 이유: CLAUDE.md CRITICAL — 감사 로그는 DB가 같은 트랜잭션에서 쓴다(ADR-055).
- 028·029를 고치지 마라. 이유: 적용된 마이그레이션 — CHECK 교체는 새 번호에서.
- `audit_log`의 UPDATE·DELETE 거부 트리거를 건드리지 마라. 이유: 감사 행 불변이 ADR-055 결정의 핵심이다.
- `test_invalid_values_are_rejected`의 거부 검사를 지우지 마라 — 예시 값만 바꾼다. 이유: CLAUDE.md(검증 약화 금지).
- 기존 테스트를 깨뜨리지 마라
