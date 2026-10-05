# Step 8: audit-ui

관리 메뉴에 「감사 로그」를 추가하고 `/admin/audit` 화면을 만든다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- API(step 7): `GET /api/admin/audit?actor=&action=&limit=&before_id=` → `{"items": AuditEntry[], "next_before_id": number | null}`. 관리자 세션 전용 — 일반 사용자는 403 "관리자 권한이 필요합니다.", 익명 401.
- `AuditEntry`: `id, occurred_at, action, actor (string|null), actor_via ("session"|"token"|"mcp"|"cli"|"share"|"worker"|null), db_role, document_id (string|null), document_title (string|null), detail (object)`.
- `detail` 모양: `text_updated {version}`, `access_changed {kind:"visibility", before, after}` 또는 `{kind:"grant", change:"added"|"removed", grantee_type:"user"|"group", grantee}`, `group_member_changed {change, group, user}`, `original_replaced`·`original_downloaded {file_version}` (+ 공유일 때 `share_name`).
- 명세서 시험항목(이 문구가 구현 계약이다 — 문구에 맞춘다):
  - 관리자가 메뉴의 「감사 로그」를 누르면 기록이 **시각·사용자·동작·대상 문서 제목**과 함께 **최신순**으로 표시됨
  - 사용자·동작으로 걸러 보면 그 조건의 기록만 표시됨
  - 일반 사용자는 메뉴에 「감사 로그」가 없고, 주소를 직접 열면 **"관리자 권한이 필요합니다."**가 표시됨
  - 문서를 만들고 텍스트를 편집하면 **「문서 생성」**과 **「텍스트 수정(v2)」** 기록이 그 사용자 이름으로 남음
  - 열람 범위를 「조직 공개」에서 「제한」으로 바꾸면 **「열람 범위 변경」** 기록에 이전·이후 값이 남음
  - 관리자가 그룹에 사용자를 넣거나 빼면 **「그룹 구성원 변경」** 기록에 그룹 이름·대상 사용자·수행한 관리자가 남음
  - 원본을 교체하거나 내려받으면 **「원본 교체」·「원본 내려받기」** 기록이 판 번호와 함께 남음
  - 문서를 삭제해도 그 문서의 기록은 남고, **「문서 삭제」** 기록에 삭제된 문서의 제목이 표시됨
- 표기 규칙:

  | action | 동작 칸 | 덧붙는 설명 |
  |---|---|---|
  | `document_created` | 문서 생성 | — |
  | `text_updated` | `텍스트 수정(v{version})` | — |
  | `document_deleted` | 문서 삭제 | — |
  | `access_changed` (visibility) | 열람 범위 변경 | `조직 공개 → 제한` (기존 열람 범위 표기: public=「조직 공개」, private=「제한」 — 프런트에 이미 있는 매핑을 재사용) |
  | `access_changed` (grant) | 열람 범위 변경 | `사용자 bob 추가` / `그룹 재무팀 제거` |
  | `group_member_changed` | 그룹 구성원 변경 | `재무팀에 bob 추가` / `재무팀에서 bob 제거` |
  | `original_replaced` | `원본 교체(판 {file_version})` | — |
  | `original_downloaded` | `원본 내려받기(판 {file_version})` | — |

- 사용자 칸: `actor`가 있으면 그 이름. 없으면 `actor_via`로 — `share` → `공유: {detail.share_name}`, `worker` → `시스템(텍스트 인식)`, `cli` → `운영자 CLI`, 그 밖(NULL) → `직접 접속({db_role})`.
- 대상 문서 제목: 있으면 제목 텍스트. **문서 링크로 만들지 마라** — 관리자는 그 문서를 열람하지 못할 수 있다(ADR-040). 없으면(그룹 구성원 변경) `—`.
- 시각: 기존 화면의 날짜 표기 방식을 따른다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 화면 규칙
- `frontend/src/app/admin/groups/page.tsx`, `page.test.tsx` — 관리 화면 선례(관리자 확인, 로딩, 오류 문구, `useUnmountSignal`)
- `frontend/src/app/admin/users/page.tsx` — 또 하나의 선례
- `frontend/src/components/SiteHeader.tsx`, `SiteHeader.test.tsx` — 관리자 메뉴(`auth.is_admin` 분기)
- `frontend/src/lib/api.ts`, `api.test.ts`, `frontend/src/lib/types.ts` — API 함수·타입 선례(`listGroups`, `listUsers`, `ApiError`)
- 열람 범위 표기 매핑이 있는 파일(`grep -rn '조직 공개' frontend/src`)

## 작업

### 1) 테스트 먼저

- `frontend/src/lib/api.test.ts`: `listAudit({actor, action, beforeId, limit}, signal)`가 쿼리 문자열을 올바로 만들고(빈 값은 넣지 않음) 응답을 돌려준다.
- `frontend/src/components/SiteHeader.test.tsx`: 관리자에게 「감사 로그」 링크(`/admin/audit`)가 보이고, 일반 사용자에게는 없다.
- 새 `frontend/src/app/admin/audit/page.test.tsx`:
  1. 관리자: 항목들이 받은 순서(최신순)대로 시각·사용자·동작·대상 문서 제목과 함께 표시된다.
  2. 위 표기 규칙 표의 각 행이 그대로 보인다(`텍스트 수정(v2)`, `조직 공개 → 제한`, `원본 교체(판 2)`, `재무팀에 bob 추가` 등).
  3. 사용자 칸 대체 표기 4종(`공유: …`, `시스템(텍스트 인식)`, `운영자 CLI`, `직접 접속(openarchive)`).
  4. 사용자·동작 필터를 바꾸면 그 조건으로 다시 요청한다.
  5. `next_before_id`가 있으면 「더 보기」 버튼이 있고, 누르면 `before_id`로 요청해 뒤에 붙인다. null이면 버튼이 없다.
  6. 일반 사용자로 열면 "관리자 권한이 필요합니다."가 보이고 API를 부르지 않는다.
  7. 대상 문서 제목이 링크(`<a>`)가 아니다.

### 2) 구현

- `frontend/src/lib/types.ts`: `AuditEntry`, `AuditPage`, `AuditAction` 타입.
- `frontend/src/lib/api.ts`: `listAudit`.
- `frontend/src/app/admin/audit/page.tsx`: 표(시각·사용자·동작·대상 문서 제목), 사용자 필터(`listUsers`로 목록, 「전체」 포함), 동작 필터(7개 + 「전체」), 「더 보기」. 표기 함수는 페이지 안에 두거나 `lib`에 두되 테스트가 닿게 한다.
- `SiteHeader.tsx`: 관리자 메뉴에 「감사 로그」.
- 정적 내보내기 결과(`backend/openarchive/static`)가 저장소에 있다면 기존 절차대로 갱신한다(CI에 static drift 검사가 있다 — `scripts/check.sh`와 CI 설정에서 방법을 확인한다).

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: ① 사용자 칸 대체 표기에서 `worker` 분기를 지우면 테스트 3 실패 ② 메뉴 링크를 관리자 분기 밖으로 옮기면 SiteHeader 테스트 실패.
3. UI_GUIDE 규칙(문구·색·간격)을 따랐는가 확인한다. 사용자 대상 문구에 "실시간"·"항상 최신"을 쓰지 않았는가.
4. static drift 검사를 통과하는가 확인한다.
5. `phases/m23-audit-log/index.json`의 step 8을 갱신한다.

## 금지사항

- 대상 문서 제목을 문서 상세 링크로 만들지 마라. 이유: 관리자는 문서를 열람하지 못한다(ADR-040) — 링크는 존재하지 않는 페이지로 가거나, 볼 수 없는 문서를 열려는 경로가 된다.
- 감사 행을 수정·삭제하는 UI를 만들지 마라. 이유: 변경 불가가 명세서 항목이다.
- 백엔드 코드를 고치지 마라. 이유: step 7까지의 범위다. API가 부족하면 `blocked`로 멈추고 사유를 적어라.
- 기존 테스트를 깨뜨리지 마라
