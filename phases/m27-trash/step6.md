# Step 6: trash-ui

휴지통 화면 `/trash`, 사이드바 링크, 삭제·영구 삭제 확인창 문구, 감사 로그 표기를 만들고 정적 산출물을 갱신한다.

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

- `/docs/UI_GUIDE.md` — 화면 규칙·문구 원칙
- `/docs/ADR.md` — ADR-060 결정 3(확인창 문구 두 개)
- `frontend/src/components/DocumentActions.tsx` — 지금 삭제 확인창 "문서를 삭제하시겠습니까? 청크와 벡터도 함께 삭제됩니다."
- `frontend/src/app/page.tsx` — 사이드바(`<aside>` 안 `FolderTree`, 로그인 사용자만)
- `frontend/src/app/admin/audit/page.tsx` — `ACTION_LABEL`(21행 `document_deleted: "문서 삭제"`), 동작 필터 콤보박스
- `frontend/src/lib/api.ts`(597행 `deleteDocument`), `frontend/src/lib/types.ts`(276행 감사 동작 유니온), `frontend/src/lib/limits.ts`(정적 빌드라 서버 설정을 읽지 못하는 상수의 선례)
- `frontend/src/components/FolderHeader.tsx` — 폴더 삭제 확인·오류 표시 선례
- step 5가 만든 API: `GET /api/documents/trash` → `[{id, title, deleted_at, purge_at}]`, `POST /api/documents/{id}/restore`, `DELETE /api/documents/{id}?permanent=true`

## 작업

### 1) 테스트 먼저 (vitest + Testing Library, 기존 `*.test.tsx` 방식)

1. `DocumentActions.test.tsx`: 「삭제」를 누르면 `window.confirm`이 정확히 `"휴지통으로 옮깁니다. 30일 뒤 영구 삭제됩니다."`로 불리고, 취소(false)면 API를 부르지 않는다. 확인하면 `DELETE /api/documents/{id}`(쿼리 없음)를 부르고 `/`로 이동한다(TC 「문서 삭제」 두 항목).
2. 새 `app/trash/page.test.tsx`: 휴지통 목록이 제목·삭제 일시·영구 삭제 예정일과 함께 나온다(TC 「휴지통 보기」). 비었으면 빈 상태 문구. 「복원」을 누르면 `POST …/restore` 뒤 목록에서 빠진다. 「영구 삭제」를 누르면 `window.confirm`에 `"삭제하면 되돌릴 수 없습니다"`가 들어가고, 확인하면 `DELETE …?permanent=true` 뒤 목록에서 빠진다(TC 「영구 삭제」). 취소면 아무 요청도 없다.
3. `app/page.test.tsx`(또는 사이드바를 그리는 컴포넌트 테스트): 로그인 사용자에게 `/trash`로 가는 「휴지통」 링크가 폴더 트리 아래에 있고, 비로그인에는 없다.
4. `app/admin/audit/page.test.tsx`: `document_trashed` → 「휴지통 이동」, `document_restored` → 「복원」, `document_deleted` → 「영구 삭제」로 표기되고 동작 필터에 세 값이 있다(TC 「감사 로그 · 변경 기록 대상」). 기존 `"문서 삭제"` 단언은 「영구 삭제」로 고친다.
5. `lib/api.test.ts`: `listTrash`·`restoreDocument`·`purgeDocument`가 위 경로·메서드를 부른다.

### 2) 구현

- `lib/api.ts`: `listTrash(): Promise<TrashItem[]>`, `restoreDocument(id)`, `purgeDocument(id)`. `lib/types.ts`: `TrashItem`, 감사 동작 유니온에 두 값.
- `lib/limits.ts`(또는 같은 성격의 상수 자리): `TRASH_NOTICE = "휴지통으로 옮깁니다. 30일 뒤 영구 삭제됩니다."` — 주석에 "백엔드 `TRASH_RETENTION_DAYS` 기본값과 같은 값, 정적 빌드라 운영자가 바꾼 값을 읽지 못한다, 실제 예정일은 휴지통 목록의 `purge_at`이 권위".
- `DocumentActions.tsx`: 확인 문구 교체.
- 새 `app/trash/page.tsx`: `RequireAuth`로 감싼 표(제목·삭제 일시·영구 삭제 예정일·「복원」·「영구 삭제」). 제목은 휴지통 문서라 상세 링크를 걸지 않는다(상세는 404다). 날짜 표기는 기존 화면의 날짜 포맷을 재사용한다.
- `app/page.tsx` 사이드바: `FolderTree` 아래 「휴지통」 링크.
- `app/admin/audit/page.tsx`: `ACTION_LABEL` 갱신.
- 끝으로 `npm run build:static`으로 `backend/openarchive/static`을 갱신한다(CLAUDE.md 개발 프로세스 — CI는 어긋남을 고쳐 주지 않는다).

## Acceptance Criteria

```bash
cd frontend && npm run lint && npm test
cd frontend && npm run build:static
cd backend && .venv/bin/pytest tests/test_frontend.py -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 문구 대조: 확인창 두 문구와 감사 표기 세 개가 공통 배경의 시험항목 표와 **글자 그대로** 같은지 `grep -rn` 으로 확인한다.
3. 아키텍처 체크리스트: 사용자 대상 문구에 "항상 최신"·"실시간"이 없는가, TypeScript strict 위반이 없는가.
4. `phases/m27-trash/index.json`의 step 6을 갱신한다.

## 금지사항

- 문서 목록에 휴지통 문서를 회색·🔒 등으로 섞어 보이지 마라. 이유: 휴지통은 별도 화면이다(D9) — 목록은 술어가 이미 뺀다.
- 휴지통 목록의 영구 삭제 예정일을 프론트에서 `deleted_at + 30일`로 계산하지 마라. 이유: 운영자가 보존 기간을 바꾸면 틀린다 — 서버의 `purge_at`을 쓴다.
- 「원문」이라는 말을 쓰지 마라. 이유: CLAUDE.md — 원본 파일/문서 텍스트를 구분한다.
- 기존 테스트를 깨뜨리지 마라
