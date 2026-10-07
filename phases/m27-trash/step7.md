# Step 7: trash-docs

ADR-060의 「구현 때 정함」을 닫고 운영·아키텍처 문서에 휴지통을 반영한다.

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

- `/docs/ADR.md` — ADR-060(특히 「구현 때 정함」), ADR-053(PITR), ADR-055 「구현 때 정함」의 형식
- `/docs/OPERATIONS.md` — 환경변수 표(`JOB_LEASE_SECONDS` 행 선례), 백업·복원 절차의 삭제 관련 문구
- `/docs/ARCHITECTURE.md` — `documents` 스키마·감사 동작 목록·워커 루프 설명
- `/docs/PRD.md` §5 C1·§7 — 휴지통 반영 여부(이미 반영돼 있으면 고치지 않는다)
- 이 phase의 step 0~6 산출물: `migrations/032_trash_tables.sql`, `033_trash_audit_triggers.sql`, `services/trash.py`, `worker.py`, `config.py`, `api/documents.py`, `frontend/src/app/trash/page.tsx`

## 작업

1. ADR-060 「구현 때 정함」을 「구현 때 정함 — 2026-10-07 사용자 결정(#198)으로 닫았다」 형식(ADR-055 선례)으로 바꾸고 네 가지를 적는다: ① 휴지통 화면은 별도 `/trash`, 문서 화면 사이드바 링크 ② 마이그레이션 032(컬럼)·033(감사) ③ 비우기는 워커 폴링 주기마다 ④ `TRASH_RETENTION_DAYS`는 양의 정수만, 0 이하는 기동 거부. 덧붙여 구현에서 정한 것: 술어 밖 예외(공유 관리 목록 숨김·가져오기 중복 판정 제외)와 `deleted_at` 문자열 위치를 고정한 아키텍처 테스트, 영구 삭제는 휴지통 밖 문서에도 가능(#189 `--permanent`용), 휴지통 API는 토큰 허용.
2. `OPERATIONS.md` 환경변수 표에 `TRASH_RETENTION_DAYS` | `30` | 설명(워커가 폴링마다 영구 삭제, 0 이하 기동 거부, 화면 확인 문구는 기본값 30일 고정) 한 행. PITR 절차에서 "문서를 삭제" 같은 표현이 영구 삭제를 뜻하면 「영구 삭제」로 고친다.
3. `ARCHITECTURE.md`: `documents.deleted_at`, 감사 동작 두 개, 워커 루프의 비우기 단계를 기존 서술 자리에 한두 줄씩.
4. 사용자 대상 문구 원칙을 지킨다 — "실시간"·"항상 최신" 금지.

## Acceptance Criteria

```bash
grep -n "TRASH_RETENTION_DAYS" docs/OPERATIONS.md docs/ADR.md
grep -n "deleted_at" docs/ARCHITECTURE.md
grep -n "document_trashed" docs/ARCHITECTURE.md
cd backend && .venv/bin/pytest tests/test_architecture.py -q
```

## 검증 절차

1. 위 AC 커맨드를 실행하고 각 grep이 한 줄 이상 내는지 본다.
2. ADR-060 본문의 결정 1~8과 새로 적은 「구현 때 정함」이 서로 어긋나지 않는지 읽어 확인한다.
3. `phases/m27-trash/index.json`의 step 7을 갱신한다.

## 금지사항

- ADR-060의 결정 본문(1~8)·기각한 대안을 고쳐 쓰지 마라. 이유: 결정 기록은 개정 표기로만 바꾼다 — 이 step은 「구현 때 정함」을 닫는 것이다.
- `CLAUDE.md`를 고치지 마라. 이유: 휴지통 규칙은 이미 반영돼 있다(docs/practice-audit 선반영).
- 기능명세서 문구를 바꾸지 마라. 이유: 제출된 명세서는 계약이다.
- 기존 테스트를 깨뜨리지 마라
