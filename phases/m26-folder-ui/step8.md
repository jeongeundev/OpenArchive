# Step 8: search-folder-filter

검색 화면에 폴더 필터를 넣는다. 고른 폴더는 검색(`POST /api/search`)과 답변(`POST /api/ask`, step 0)에 같은 `folder_id`로 간다.

## 공통 배경 — m26-folder-ui 설계 결정 (모든 step 같음)

이슈 #187 c, 폴더 **화면**이다. 폴더 백엔드는 PR #203(m25-folders)으로 main에 있고 API는 확정됐다 — `docs/ARCHITECTURE.md`의 API 표(「`GET /api/folders`」 행 부근)와 `backend/openarchive/api/folders.py`·`api/schemas.py`가 정본이다. 근거 ADR은 ADR-054(폴더)·ADR-044(열람 모델)·ADR-055(감사). 폴더 단위 외부 공유·폴더 소유자 이전(#200)·휴지통(#198)·CLI `import --keep-folders`는 **이번 범위가 아니다.**

**기능명세서는 제출되어 고칠 수 없다 — 문구가 구현 계약이다.** 각 step에 붙은 「명세서 TC」 원문을 글자 그대로 충족하는 화면을 만들고, 그 문구를 단언하는 테스트를 쓴다.

### 백엔드 계약 요약 (이미 구현됨 — 바꾸지 않는다, step 0 제외)

- `GET /api/folders` → `Folder[]` 평평한 목록. `Folder = {id, parent_id, name, created_by, document_count, scope: {visibility: "public"|"private", users: string[], groups: string[]}, inherited, can_manage, can_change_access}`. `scope`는 최상위 폴더의 실효 범위(하위 폴더도 최상위 것을 그대로 싣고 `inherited: true`). `document_count`는 그 폴더에 **직접 든**, 조회자가 볼 수 있는 문서 수. `can_manage` = 이름 변경·삭제 가능(만든 사람 또는 관리자), `can_change_access` = 범위 변경 가능(최상위 폴더를 만든 사람만, 관리자 불가).
- `POST /api/folders {name, parent_id?}` → 201 `Folder`(새 최상위는 조직 공개). `PATCH /api/folders/{id} {name}` → `Folder`. `DELETE /api/folders/{id}` → 204, 비어 있지 않으면 409 `"폴더가 비어 있지 않습니다."`, 권한 없으면 403 `"폴더를 관리할 권한이 없습니다."`. 이름 규칙 위반 400, 같은 부모 아래 같은 이름 409.
- `GET /api/folders/{id}/access` → `{visibility, users, groups}` — 최상위 폴더를 만든 사람만(그 밖 403). `PUT /api/folders/{id}/access {visibility, users, groups}` — **세션 전용**, 만든 사람만(관리자 포함 그 밖 403 `"폴더를 관리할 권한이 없습니다."`), 하위 폴더 400 `"하위 폴더는 상위 폴더의 열람 범위를 따릅니다."`.
- 문서: 목록·건수 `GET /api/documents?folder_id=`·`/count?folder_id=`(직접 든 문서만). 업로드 Form·`POST /api/documents/text`에 선택 `folder_id` — **폴더를 주면 `visibility`·`grant_users`·`grant_groups`를 보내면 안 된다(400).** `PUT /api/documents/{id}/folder {folder_id: uuid|null}` → `DocumentDetail`(소유자만, 토큰 허용). `GET /api/documents/{id}`의 `folder: {id, name, path: [{id, name}]} | null`(조회자가 그 폴더를 볼 수 있을 때만 — `path`는 최상위부터 자기 자신까지). `GET /api/documents/{id}/access` → `{visibility, users, groups, follows_folder, folder, folder_scope}`. `PUT /api/documents/{id}/access`에 `follows_folder` — `{follows_folder: true}`만 보내면 「폴더 범위 따름」(이때 `visibility`·`users`·`groups`를 함께 보내면 400), `{follows_folder: false, visibility, users, groups}`는 「개별 지정」.
- 검색 `POST /api/search`에 선택 `folder_id`(그 폴더와 하위 폴더의 문서로 직접 결과를 좁힌다).
- 감사: `folder_access_changed` detail `{kind: "visibility", folder_id, folder_name, before, after}` 또는 `{kind: "grant", change: "added"|"removed", grantee_type: "user"|"group", grantee, folder_id, folder_name}` — `document_title`은 null. `access_changed`에 새 kind 둘: `{kind: "inherit", before, after}`(값 `"folder"`=폴더 범위 따름 / `"own"`=개별 지정), `{kind: "folder", before, after}`(폴더 이름, 폴더 밖이면 null).

### 화면 결정 (사용자 승인 10/6)

- **B1 트리 위치·선택 상태**: 홈(`/`) 왼쪽에 폴더 트리(맨 위 「전체 문서」·「새 폴더」, 각 폴더 옆 문서 수). 좁은 화면에서는 목록 위로 쌓인다. 선택 폴더는 URL `?folder=<id>`에 둔다(상세의 폴더 경로 링크·뒤로 가기). `useSearchParams`는 정적 export에서 Suspense 경계가 필요하다(선례 `src/app/documents/[id]/page.tsx`).
- **B2 폴더 조작**: 폴더를 고르면 목록 위 **폴더 헤더** — 경로, 범위 표시, 버튼 「하위 폴더」·「이름 변경」·「삭제」(+최상위면 「열람 범위」). 이름 입력은 모달 없이 그 자리 입력칸. 삭제는 `window.confirm` 후 서버 오류 문구를 그대로 `role="alert"`로.
- **B10 거부는 서버가 보인다**: 폴더를 볼 수 있는 사람에게 「이름 변경」·「삭제」·(최상위) 「열람 범위」를 **숨기지 않는다.** 권한이 없으면(`can_manage`/`can_change_access` false) 버튼 곁에 「폴더를 만든 사람만 바꿀 수 있습니다」를 미리 보이고, 그래도 저장하면 서버 403 문구 「폴더를 관리할 권한이 없습니다.」를 그대로 보인다. 이유: 명세서가 「저장하면 거부됨」이라 시도할 수 있어야 하고, 서버 경계를 시연으로 증명한다. (문서 열람 범위 패널은 이와 달리 지금처럼 소유자에게만 보인다.)
- **B3 폴더 범위 패널**: 최상위 폴더에만. 「조직 공개 / 제한」 + `GranteePicker`(기존 컴포넌트). 대상 없는 「제한」에는 「대상을 고르지 않으면 폴더를 만든 사람만 봅니다」, 저장 근처에 「폴더 범위를 따르는 문서(다른 사람이 넣은 문서 포함)에 바로 적용됩니다」. 하위 폴더는 패널 없이 헤더에 「상위 폴더 범위 따름(…)」.
- **B4 확인 안내**: 문서 **이동**은 「폴더 범위 따름」 문서이고 이동 전후 실효 범위가 다를 때만 `window.confirm("열람 범위가 A에서 B로 바뀝니다. …")`. 「개별 지정」 문서는 범위가 안 바뀌므로 확인하지 않는다. **업로드**는 폴더를 고르면 열람 범위 칸이 「폴더 범위 따름(…)」으로 바뀌는 것이 안내이고(확인 대화상자 없음), 남이 만든 폴더면 「폴더를 만든 사람이 범위를 바꾸면 이 문서에도 적용됩니다」를 덧붙인다.
- **B5 문서 상세**: 폴더 경로(「인사/채용」, 각 조각이 `/?folder=<id>` 링크)는 `folder`가 있을 때만 — `null`이면 아무것도 그리지 않는다(🔒·「알 수 없는 폴더」 같은 자리도 금지, ADR-027). 폴더 바꾸기는 소유자에게만. 「폴더 범위 따름 / 개별 지정」 전환은 기존 `AccessPanel` 안.
- **B6 폴더 고르기**: 업로드·이동·검색이 공용 `FolderSelect` — 평평한 `<select>`, 라벨은 전체 경로 `인사/채용`(구분자 `/`, 공백 없음 — 명세서 표기), 트리 순서(부모 다음 자식, 같은 층은 이름순).
- **범위 라벨 형식**: `scope.visibility === "public"` → 「조직 공개」. `"private"` → 대상이 없으면 「제한」, 있으면 「제한 · 」 + 그룹들 + 사용자들을 `, `로 이은 것(예: 「제한 · 사업팀」). 「폴더 범위 따름(제한 · 사업팀)」, 「상위 폴더 범위 따름(조직 공개)」처럼 괄호로 감싼다. 기존 `VISIBILITY_LABEL`(`src/lib/types.ts`)을 쓴다.
- **B7 갱신**: 폴더 목록은 `useFolders` 훅 하나. 폴더 생성·이름 변경·삭제·범위 저장·업로드·문서 이동 뒤 다시 불러온다(문서 수가 바뀐다). 문서 목록에 폴더 열을 넣지 않는다(목록 요약에 `folder_id`가 없는 것은 누출 방지 설계다).
- **B8 ask 폴더 필터**: 검색 화면의 폴더 필터는 search와 ask(답변) 둘 다에 같은 `folder_id`로 간다(step 0이 ask 백엔드에 더한다).
- **B9 감사 라벨**: `folder_access_changed` = 「폴더 열람 범위 변경」. 설명: kind visibility → 「조직 공개 → 제한」, kind grant → 「그룹 사업팀 추가」/「사용자 lee 제거」. 대상 칸 → 「폴더 「RFP」」(`detail.folder_name`). `access_changed` kind inherit → 「폴더 범위 따름 → 개별 지정」, kind folder → 「폴더 「인사」 → 「RFP」」(null은 「폴더 없음」).

### 프론트 관례

- `src/lib/api.ts`의 `request<T>()`·`ApiError(status, detail)`를 쓴다. JSON body는 `headers: {"Content-Type": "application/json"}` + `JSON.stringify`. id는 `encodeURIComponent`. 204는 `{parse: false}`.
- 테스트는 vitest + testing-library. api 모듈을 `vi.mock`하지 말고 `vi.stubGlobal("fetch", vi.fn(...))`로 URL별 응답을 준다(선례 `src/components/AccessPanel.test.tsx`, `src/components/UploadDropzone.test.tsx`, `src/__tests__/page.test.tsx`). `afterEach(() => vi.unstubAllGlobals())`.
- 스타일은 기존 컴포넌트의 Tailwind 클래스(어두운 배경 `neutral-*`, 강조 `#0ea5e9`)를 따른다. 화면 문구 규칙은 `docs/UI_GUIDE.md`.
- 사용자 대상 문구에 「항상 최신」·「실시간 동기화」를 쓰지 않는다(CLAUDE.md).

## 명세서 TC (글자 그대로 충족 — 테스트로 단언)

- 검색 화면에서 폴더 「인사」를 고르고 검색하면 직접 맞은 결과는 「인사」와 그 하위 폴더의 문서만 나옴 — 화면은 `folder_id`를 보내고, 하위 포함·직접 결과 한정은 서버가 한다.

## 읽어야 할 파일

- `frontend/src/components/SearchForm.tsx`·`SearchForm.test.tsx`
- `frontend/src/lib/useSearch.ts`(`SearchInput`), `src/app/search/page.tsx`·`page.test.tsx`(`lastInput`을 `AnswerPanel`에 넘기는 흐름), `src/components/AnswerPanel.tsx`, `src/lib/useAsk.ts`
- step 0: `backend/openarchive/api/schemas.py`의 `AskRequest.folder_id`
- step 2 산출물: `src/lib/api.ts`(`searchBody`의 `folder_id`), `src/lib/useFolders.ts`, `src/components/FolderSelect.tsx`

## 작업

- `SearchInput`에 `folderId: string | null`. `SearchForm`의 필터 그리드에 `FolderSelect`(라벨 「폴더」, 없음 라벨 「전체 폴더」). 로그인 상태에서만 폴더 목록을 불러오고, 목록이 비었거나 불러오지 못하면 select를 그리지 않는다(공유 링크 열람자는 `/api/folders`가 403이다 — 오류 문구를 띄우지 않는다).
- `search`·`ask` 요청 body에 같은 `folder_id`가 실린다(step 2의 `searchBody` 경유). 필터 칸 근처에 「관계로 이어진 결과는 폴더 밖 문서일 수 있습니다」 같은 짧은 설명을 둘지 판단한다 — 서버 계약(ARCHITECTURE 「폴더 필터는 직접 결과에만 걸린다」)과 어긋나지 않게.

### 테스트 먼저
- `SearchForm.test.tsx`: 폴더를 고르고 제출하면 `onSearch`에 `folderId`.
- `search/page.test.tsx`(또는 api 단 테스트): 검색 요청 body와 답변 요청 body에 같은 `folder_id`.
- 폴더 목록 403이면 select가 없고 검색은 그대로 된다.

## Acceptance Criteria

```bash
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. `phases/m26-folder-ui/index.json`의 step 8을 갱신한다.

## 금지사항

- 검색 결과를 화면에서 폴더로 거르지 마라. 이유: CLAUDE.md CRITICAL — 정형 필터는 단일 SQL 안에서.
- 백엔드를 고치지 마라. 이유: step 0에서 끝났다.
- 기존 테스트를 깨뜨리지 마라
