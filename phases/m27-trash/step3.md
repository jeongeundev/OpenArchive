# Step 3: trash-service

휴지통 이동·목록·복원·영구 삭제·만료 비우기 서비스 `services/trash.py`를 만든다.

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

- `/docs/ADR.md` — ADR-060, ADR-046(원본 판), ADR-055 결정 3(행위자는 `SET LOCAL`)
- `backend/openarchive/services/documents.py` — `delete_document`(1508행 근처), `_load_for_write`, `DocumentNotFound`, `get_document`의 반환 형태
- `backend/openarchive/services/visibility.py` — step 2의 `NOT_TRASHED`
- `backend/openarchive/services/audit.py` — `set_actor`(트랜잭션 안에서만)
- `backend/openarchive/api/documents.py` 478행 `DELETE /documents/{id}` — 지금 `service.delete_document`를 부른다(라우터는 step 5가 바꾼다)
- `backend/tests/test_documents.py` — 삭제 연쇄 테스트(청크·잡·원본 판) 선례
- `backend/openarchive/migrations/032_trash_tables.sql`, `033_trash_audit_triggers.sql` — step 0·1

## 작업

### 1) 테스트 먼저 — 새 파일 `backend/tests/test_trash.py`

1. `trash_document`: 소유자가 부르면 `deleted_at`이 채워지고 `list_documents`에서 사라진다. 청크·`document_edges`·태그·`folder_id`·`follows_folder`·`visibility`·부여 행이 그대로다. 이미 휴지통이면 `DocumentNotFound`(술어가 가린다). 소유자가 아닌 사용자(조직 공개라 볼 수 있는 사용자 포함)·관리자 → 각각 지금 삭제와 같은 거부(`_load_for_write`의 예외를 그대로 쓴다).
2. `list_trash(owner)`: 자기 휴지통 문서만, 최근 삭제순으로 `id`·`title`·`deleted_at`·`purge_at`(= `deleted_at + retention_days`)을 준다. 다른 사용자가 지운 문서, 관리자 계정으로 불렀을 때 남의 문서는 없다(TC 「권한」). 휴지통 밖 문서도 없다.
3. `restore_document`: 소유자만. `deleted_at`이 NULL로 돌아오고, 같은 폴더·열람 범위·태그로 `list_documents`에 다시 나오며, **`embedding_jobs`가 새로 생기지 않고** 바로 검색된다(TC 「복원」). 휴지통에 없는 문서·남의 문서·없는 id → `DocumentNotFound`.
4. `purge_document`: 소유자만. 휴지통 안·밖 모두 하드 삭제되고 청크·임베딩 잡·원본 판(`document_files`)·버전이 함께 지워진다(TC 「삭제 연쇄」). 남의 문서(관리자 포함)·없는 id → `DocumentNotFound`.
5. `purge_expired(conn, *, retention_days)`: `deleted_at < now() - retention_days`인 문서만 하드 삭제하고 건수를 반환한다. 경계 직전 문서·휴지통 밖 문서는 남는다. 감사 행이 `actor_via='worker'`, `actor` NULL로 남는다(ADR-060 결정 4 — 워커 연결은 step 4). 테스트에서는 `deleted_at`을 과거로 직접 UPDATE해 만든다.
6. 각 동작 뒤 감사 행: `trash_document` → `document_trashed`, `restore_document` → `document_restored`, `purge_document` → `document_deleted`(트리거가 쓴다 — 서비스는 기록하지 않는다).

### 2) 구현 — 새 파일 `backend/openarchive/services/trash.py`

```python
async def trash_document(conn, document_id: UUID, *, user_id: str) -> None: ...
async def list_trash(conn, *, user_id: str, retention_days: int) -> list[dict]: ...
async def restore_document(conn, document_id: UUID, *, user_id: str) -> dict: ...   # get_document와 같은 요약 형태
async def purge_document(conn, document_id: UUID, *, user_id: str) -> None: ...
async def purge_expired(conn, *, retention_days: int) -> int: ...
```

- `trash_document`는 `documents._load_for_write`로 쓰기 권한을 확인한 뒤 `UPDATE documents SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL`.
- `restore_document`·`purge_document`·`list_trash`는 술어를 쓰지 않고 **`owner_id = user_id`로만** 판정한다(휴지통 문서는 술어로 보이지 않는다). 조건이 맞지 않으면 전부 `DocumentNotFound` — 남의 휴지통 문서의 존재를 알리지 않는다.
- `purge_expired`는 한 트랜잭션 안에서 `audit.set_actor(conn, actor=None, via="worker")` 뒤 DELETE한다. 호출자의 연결이 autocommit이어도 동작하도록 함수 안에서 `conn.transaction()`을 연다.
- `documents.delete_document`의 호출부를 전수 찾아(`grep -rn delete_document backend/openarchive`) — 라우터 외에 없으면 이 step에서는 남겨 두고 summary에 적는다(라우터 교체는 step 5, 거기서 쓰이지 않게 되면 지운다).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_trash.py tests/test_trash_visibility.py tests/test_documents.py tests/test_audit_log.py tests/test_architecture.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `restore_document`·`purge_document`의 `owner_id` 조건을 빼면 테스트 3·4의 남의 문서 경우가, `purge_expired`의 기간 비교 방향을 뒤집으면 테스트 5가 실패해야 한다. 확인 뒤 되돌린다.
3. `phases/m27-trash/index.json`의 step 3을 갱신한다.

## 금지사항

- 휴지통 이동 때 청크·관계·잡을 지우거나, 복원 때 재임베딩·`enqueue_all_edge_jobs`를 부르지 마라. 이유: D2 — 복원은 즉시 검색되어야 한다(ADR-060 결정 2, 기각한 대안).
- 관리자에게 남의 휴지통 조회·복원·영구 삭제를 허용하지 마라. 이유: D3 — 관리자 비열람(ADR-040·044). 관리자의 복구 경로는 PITR이다.
- `embedding_jobs`·`document_edges`·`audit_log`에 INSERT하지 마라. 이유: CLAUDE.md CRITICAL.
- 세션 `SET`으로 행위자를 넘기지 마라. 이유: OpenProxy 풀 백엔드로 샌다 — `set_actor`(SET LOCAL)만.
- 기존 테스트를 깨뜨리지 마라
