# Step 1: trash-audit

휴지통 이동·복원을 트리거가 감사 로그에 남기게 하는 마이그레이션 `033_trash_audit_triggers.sql`을 쓴다.

## 공통 배경 — m27-trash 설계 결정 (모든 step 같음)

이슈 #198 휴지통이다. 근거 ADR-060(채택, 2026-10-06), 감사는 ADR-055, 폴더는 ADR-054. 사용자 CLI(`doc trash`·`doc delete --permanent`)는 **이 phase에서 하지 않는다** — 사용자 CLI 자체가 #189이고, #101 순서표가 그것을 #198 뒤에 둔다. MCP에는 삭제·복원 도구를 더하지 않는다(ADR-060 결정 8).

결정 (ADR-060 + 2026-10-07 사용자 결정):

- **D1 `documents.deleted_at timestamptz NULL`.** 삭제(휴지통 이동)는 이 값을 `now()`로 채우는 UPDATE다. 열람 술어 `VISIBLE_TO_USER`(`services/visibility.py`)에 `d.deleted_at IS NULL`을 넣어, 검색·관련 문서·태그 추천·군집·진단·위키링크·백링크·목록·답변 근거·MCP·공유 토큰 전 경로에서 한 번에 사라지게 한다.
- **D2 휴지통에 있는 동안 청크·관계·버전·원본 판·부여·폴더·태그·잡은 그대로 둔다.** 복원은 `deleted_at`을 NULL로 되돌리는 것뿐이다 — 재임베딩·관계 재계산 없이 바로 검색된다. `deleted_at`만 바꾸는 UPDATE는 기존 트리거(`UPDATE OF content_hash`·`embedding_status`·`extraction_status`)를 건드리지 않는다. 워커는 휴지통 문서의 잡도 지금처럼 처리한다(바꾸지 않는다). `rebuild_document_edges`도 바꾸지 않는다 — 휴지통 청크가 이웃 후보 자리를 차지하는 것은 제한 문서와 같은 비용이다(ADR-060 트레이드오프 1, 읽는 쪽 `src ∪ dst`가 술어로 거른다).
- **D3 휴지통 목록·복원·영구 삭제는 소유자만.** 소유자가 아니면(관리자 포함) 문서가 없는 것과 같다(404 `DocumentNotFound`) — 존재를 누출하지 않는다. 관리자는 남의 휴지통을 보지 못한다(ADR-040·044).
- **D4 영구 삭제**는 지금의 하드 삭제(`DELETE FROM documents` — 청크·잡·원본 판·버전 FK CASCADE)다. 소유자의 문서면 휴지통 안이든 밖이든 영구 삭제할 수 있다(#189 `doc delete --permanent`가 이 경로를 쓴다). 화면은 휴지통에서만 영구 삭제 버튼을 보인다.
- **D5 보존 기간 `TRASH_RETENTION_DAYS`, 기본 30, 양의 정수만**(0 이하는 설정 검증 오류로 기동 거부). 영구 보존이 필요하면 큰 값을 넣는다. 워커가 **폴링 주기마다**(루프의 `purge_expired_idempotency_keys` 옆 한 단계) `deleted_at < now() - 보존 기간`인 문서를 영구 삭제한다. 잡 종류를 늘리지 않는다. 행위자는 `actor_via='worker'`, `actor` NULL.
- **D6 감사**: 「휴지통 이동」 `document_trashed`, 「복원」 `document_restored`, 「영구 삭제」 `document_deleted`(기존 동작 이름 유지, 화면 표기만 「문서 삭제」→「영구 삭제」). 트리거가 `deleted_at`의 NULL→값 / 값→NULL 전이로 구분한다. 앱이 `audit_log`에 INSERT하지 않는다(CLAUDE.md CRITICAL).
- **D7 술어 밖에서 `deleted_at`을 쓰는 곳은 정해진 예외뿐이다.** ① 휴지통 서비스 자체(목록·복원·영구 삭제·만료 비우기 — 정의상 `deleted_at IS NOT NULL`) ② 공유 관리 목록(`services/shares.py` `list_shares`)은 휴지통 문서를 숨긴다 — 공유 토큰으로도 이미 보이지 않는다 ③ 가져오기 중복 판정(`services/documents.py` `find_same_original`·`find_same_text`)은 휴지통 문서를 중복으로 세지 않는다 — 다시 가져오면 새 문서가 된다. ②③은 `visibility.py`가 내보내는 조각 상수 `NOT_TRASHED`(별칭 `d`)를 쓴다. 그래서 `backend/openarchive`의 `.py` 중 문자열 `deleted_at`이 나오는 파일은 `services/visibility.py`와 `services/trash.py` 둘뿐이다 — 아키텍처 테스트로 고정한다(ADR-060 트레이드오프 2).
- **D8 바꾸지 않는 판정**: 폴더가 비었는지(`services/folders.py` `delete_folder`)와 사용자 삭제 거부(`services/auth.py` `delete_user`)는 휴지통 문서도 센다 — 지금 코드 그대로가 ADR-060 결정 6이다. 정합성 카운터(`services/system.py` `STATUS_SQL`)도 휴지통 문서를 포함해도 된다(ADR-060 트레이드오프 3). 시스템 상태·목록의 "문서 N건"은 술어를 거치는 `document_progress`·`count_documents`라 자동으로 빠진다.
- **D9 화면**: 별도 화면 `/trash`, 문서 화면 사이드바의 폴더 트리 아래에 「휴지통」 링크. 정적 빌드라 화면은 운영자가 바꾼 보존 기간을 읽지 못한다 — 확인창 문구는 기본값 30일 고정(`lib/limits.ts`와 같은 방식), 휴지통 목록의 영구 삭제 예정일은 서버가 계산해 준다.

### 기능명세서 시험항목 — 문구가 구현 계약이다 (글자 그대로 지킨다)

정본은 제출본 `notes/contest/submission/functional-spec-submit.md`(2026-10-07 제출)다. 이슈 #198 본문의 표는 가지치기 전 초안이라 다르다 — 어긋나면 제출본을 따른다.

| 대분류 | 중분류 | 시험 내용 |
|---|---|---|
| 문서 상세 | 삭제 | 「삭제」를 누르고 확인하면 문서가 휴지통으로 옮겨져 목록·검색에서 사라지고, 휴지통 화면에 나타남 |
| 문서 상세 | 삭제 | 「삭제」를 누르면 확인창에 "휴지통으로 옮깁니다. 30일 뒤 영구 삭제됩니다."가 표시되고, 「취소」하면 문서가 그대로 남음 |
| 문서 목록 | 휴지통 | 「휴지통」을 열면 내가 지운 문서가 제목·삭제 일시·영구 삭제 예정일과 함께 표시됨 |
| 문서 목록 | 휴지통 | 휴지통에 있는 문서는 문서 목록·검색·관련 문서·관계 지도·문서 진단·답변 근거·REST 목록·MCP 검색·공유 토큰 어디에도 나타나지 않음 |
| 문서 목록 | 휴지통 | 「복원」을 누르면 문서가 원래 폴더·열람 범위·태그 그대로 돌아오고, 재임베딩 없이 바로 검색됨 |
| 문서 목록 | 휴지통 | 「영구 삭제」를 누르면 확인창에 "삭제하면 되돌릴 수 없습니다"가 표시되고, 확인하면 휴지통에서도 사라짐 |
| 문서 목록 | 휴지통 | 다른 사용자가 지운 문서는 내 휴지통에 나타나지 않고, 관리자 계정의 휴지통에도 남의 문서는 없음 |
| 문서 목록 | 폴더 | 문서가 든 폴더를 삭제하면 "폴더가 비어 있지 않습니다."로 거부됨 (휴지통 문서도 "든 문서"다 — D8) |
| 관리 | 감사 로그 | 문서를 휴지통에 옮기면 「휴지통 이동」, 복원하면 「복원」, 영구 삭제하면 「영구 삭제」 기록이 남고, 영구 삭제 뒤에도 그 문서의 기록과 제목은 남음 |
| 정합성·복구 | 정합성 | 휴지통에서 영구 삭제하면 그 문서의 청크(벡터)·임베딩 작업·원본 파일이 함께 삭제됨 |
| 정합성·복구 | 백업·복원 | [Pre-condition] 복원 지점을 찍은 뒤 문서를 영구 삭제하고 새 문서를 올림(dr_restore.py mark·break) … (절차·도구는 이 phase 밖, 문서 표현만 step 7) |
| CLI 클라이언트 | 읽기 전용 토큰 | read 범위 토큰으로 … `doc delete`를 실행하면 「쓰기 권한이 필요합니다」로 거부됨 (CLI는 #189 — 이 phase는 API가 `read` 토큰의 삭제를 거부하는 것까지) |

**명세서 항목이 아닌 ADR 결정**: 30일 자동 비우기(ADR-060 결정 4, D5)는 제출본에 시험항목이 없다. 그래도 구현한다 — 확인창이 "30일 뒤 영구 삭제됩니다"라고 약속하므로 비우기가 없으면 문구가 거짓이 된다. 사용자 CLI `doc trash`·`--permanent`는 #189.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-055(감사 로그 — 결정 1~3, 「구현 때 정함」), ADR-060 결정 5
- `backend/openarchive/migrations/028_audit_tables.sql` — `audit_log`, 동작·경로 CHECK
- `backend/openarchive/migrations/029_audit_triggers.sql` — `audit_record()`, `audit_document_change()`(INSERT·DELETE·visibility 분기)
- `backend/openarchive/migrations/031_folder_audit_triggers.sql` — 동작 CHECK를 DROP→ADD로 넓힌 선례(이 step도 같은 방식)
- `backend/openarchive/migrations/032_trash_tables.sql` — step 0이 만든 `deleted_at`
- `backend/openarchive/services/audit.py` — `AUDIT_ACTIONS` 튜플
- `backend/tests/test_audit_log.py`, `backend/tests/test_audit.py` — 감사 트리거·동작 목록 테스트 선례

## 작업

### 1) 테스트 먼저 — `backend/tests/test_audit_log.py`(트리거 동작)와 `test_audit.py`(동작 목록)에 추가

1. `deleted_at` NULL→값 UPDATE 한 번에 `document_trashed` 행이 정확히 1개, 문서 id·제목이 실린다. 행위자는 같은 트랜잭션의 `set_actor`(session 등) 값이다.
2. 값→NULL UPDATE에 `document_restored` 행이 1개.
3. 값→다른 값, NULL→NULL(다른 컬럼만 바뀐 UPDATE)에는 휴지통 관련 행이 생기지 않는다.
4. 휴지통 문서를 `DELETE`하면 기존대로 `document_deleted`가 남고, 삭제 뒤에도 그 문서의 `document_trashed`·`document_deleted` 행과 `document_title`이 그대로 조회된다(FK CASCADE 없음 — TC 「감사 로그 · 변경 기록 대상」).
5. `actor_via='worker'`·`actor` NULL로 `set_actor`한 트랜잭션의 DELETE는 `actor_via='worker'`로 남는다.
6. `AUDIT_ACTIONS`에 두 동작이 있고, DB의 `audit_log_action_valid` CHECK 목록과 같다(기존 테스트가 대조한다면 그것을 따른다).

### 2) 구현 — `backend/openarchive/migrations/033_trash_audit_triggers.sql`

- `audit_log_action_valid`를 DROP 후 031 목록 + `'document_trashed'`, `'document_restored'`로 다시 ADD.
- 새 함수 `audit_document_trash_changed()` — `OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL`이면 `document_trashed`, 반대면 `document_restored`. `audit_record()`를 부른다. 기존 `audit_document_change()`의 ELSE(visibility) 분기에 섞지 마라 — 그 분기는 UPDATE를 전부 visibility 변경으로 해석한다.
- 트리거 `trg_audit_document_trash_changed AFTER UPDATE OF deleted_at ON documents FOR EACH ROW WHEN ((OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL))`.
- `services/audit.py`의 `AUDIT_ACTIONS`에 두 값을 더한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_audit_log.py tests/test_audit.py tests/test_audit_api.py tests/test_triggers.py tests/test_migrations.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 트리거의 `WHEN` 조건을 지우면 테스트 3이, 분기 방향을 뒤집으면 테스트 1·2가 실패해야 한다. 확인 뒤 되돌린다.
3. `phases/m27-trash/index.json`의 step 1을 갱신한다.

## 금지사항

- 앱 코드에서 `audit_log`에 INSERT하지 마라. 이유: CLAUDE.md CRITICAL — 감사는 DB가 쓴다(ADR-055).
- 029·031을 고치지 마라. 이유: 이미 적용된 마이그레이션이다 — 변경은 새 번호로.
- `document_deleted`의 이름을 바꾸지 마라(예: `document_purged`). 이유: 기존 감사 행과 관리 화면 필터가 그 값을 쓴다 — 표기는 화면(step 6)에서만 「영구 삭제」로 바꾼다.
- 기존 테스트를 깨뜨리지 마라
