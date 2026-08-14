# Step 3: status-guard

## 배경 — 401을 만든 변경이 화면에 에러 문구를 남긴다

바로 앞 step에서 `GET /api/system/status`가 로그인을 요구하게 됐다. 그런데
`frontend/src/app/admin/status/page.tsx`에는 인증 가드가 없다. 미로그인 상태로 이 페이지를 열면
`useSystemStatus`(2초 주기 폴링)가 401을 받고, `StatusPanel`이 *"요청에 실패했습니다. (401)"*을
띄운다 — 2초마다 반복해서.

같은 `/admin` 아래의 `users` 페이지는 이미 가드를 갖고 있다
(`frontend/src/app/admin/users/page.tsx:72-73`):

```tsx
if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
if (!auth.is_admin) return <p className="text-sm text-neutral-500">관리자 권한이 필요합니다.</p>;
```

status 화면도 같은 형태를 갖춘다. 다만 status는 **관리자 전용이 아니라 로그인만 요구**하므로
판정 조건은 `auth.is_admin`이 아니라 `auth.authenticated`다.

### 🔴 결정 1 — 훅은 조건부로 호출할 수 없다. 컴포넌트를 나눠라

**단순히 `return`을 앞에 두는 것으로는 폴링이 멈추지 않는다.** `useSystemStatus()`는 컴포넌트
최상단에서 호출되므로, 아래에서 무엇을 반환하든 이미 fetch가 시작된 뒤다. React 훅 규칙상
조건부 호출도 불가능하다.

따라서 **같은 파일 안에 모듈 레벨 컴포넌트 두 개**를 둔다:

- `default export` — `useAuth()`만 쓰고, 통과했을 때만 아래 컴포넌트를 렌더한다
- 내용 컴포넌트 — `useSystemStatus()`와 `ErrorDocuments`를 품는다. 가드를 통과해야만
  마운트되므로 미로그인 상태에서는 폴링이 **아예 시작되지 않는다**

새 파일을 `components/`에 만들 필요는 없다. 이 컴포넌트는 이 페이지 밖에서 쓰이지 않는다.

### ✅ 결정 2 — `ErrorDocuments`도 가드 안쪽이다

`ErrorDocuments`는 문서 목록 API를 부르며 그것도 로그인을 요구한다(ADR-028). 가드 밖에 두면
같은 에러 문구가 그 자리에 다시 뜬다. 내용 컴포넌트 안에 함께 넣어라.

## 읽어야 할 파일

- `frontend/src/app/admin/status/page.tsx` — 고칠 대상. 현재 10줄
- `frontend/src/app/admin/users/page.tsx:9-10, 72-73` — 가드 패턴의 본보기.
  `const { auth, loading: authLoading } = useAuth()`와 두 줄짜리 조기 반환
- `frontend/src/components/AuthProvider.tsx` — `useAuth()`가 주는 것: `{ auth, loading, setAuth }`.
  `auth`는 `AuthStatus`(`authenticated`·`username`·`is_admin`)이고 초기값은 익명, `loading`은
  `/api/auth/me` 응답 전까지 `true`
- `frontend/src/lib/useSystemStatus.ts` — 폴링 훅. 마운트 즉시 1회 + 주기 반복
- `frontend/src/lib/useSystemStatus.test.ts` — **테스트 관례의 본보기.**
  `vi.stubGlobal("fetch", fetchMock)`, `response()` 헬퍼, `flush()` 헬퍼, `vi.useFakeTimers()`
- `frontend/src/components/StatusPanel.tsx` — `status`와 `error`를 받아 그리는 표시 컴포넌트.
  **이 컴포넌트는 고치지 않는다**
- `frontend/AGENTS.md` — 이 저장소의 Next.js는 학습 데이터와 다르다. 필요하면
  `node_modules/next/dist/docs/`를 먼저 읽어라

## 작업

### 1) 테스트를 먼저 쓴다

`frontend/src/app/admin/status/page.test.tsx`(신규). vitest 설정은 include 패턴을 지정하지
않으므로 기본값이 적용되어 이 경로도 수집된다.

`AuthProvider`로 감싸 렌더하고, `fetch`를 URL로 분기하는 mock을 심는다
(`/api/auth/me` → 인증 상태, `/api/system/status` → 상태 JSON).

최소 두 개:

- **미로그인** — `/api/auth/me`가 `{authenticated: false, ...}`를 줄 때, 로그인 안내 문구가
  보이고 **`/api/system/status`로 나간 fetch가 0건**이다. 폴링이 시작되지 않았음을 fetch 호출
  URL 목록으로 단언하라. 문구만 보는 테스트는 이 step의 핵심(폴링 차단)을 놓친다.
- **로그인** — `{authenticated: true, username: "alice", ...}`일 때 `/api/system/status`가
  호출되고 `StatusPanel`의 값(예: 프로바이더 이름이나 잡 카운터)이 화면에 나타난다.

`auth.loading` 동안의 표시("불러오는 중…")도 한 개 더 단언하면 좋다 — 로딩 중에 로그인 안내가
잠깐 번쩍이는 것을 막는 조건이다.

**이 시점에 실행하면 미로그인 테스트가 실패한다** (현재는 가드가 없어 status fetch가 나간다).

### 2) `page.tsx`에 가드를 건다

- `default export`는 `useAuth()`만 쓴다. `authLoading`이면 "불러오는 중…", `!auth.authenticated`면
  로그인 안내를 반환한다. 문구·클래스는 `admin/users/page.tsx:72-73`과 맞춘다
  (`text-sm text-neutral-500`). 안내 문구는 **"로그인이 필요합니다."**
- 통과하면 내용 컴포넌트를 렌더한다. 기존 header 마크업(운영·데모 전용 / 시스템 상태 / 설명
  문단)과 `StatusPanel`·`ErrorDocuments`는 그 안으로 옮긴다.
- **`is_admin`을 요구하지 마라.** status는 관리자 전용 화면이 아니다. ADR-028이 관리자 권한을
  *계정 관리 전용*으로 한정했고, 열람 권한을 관리자에게 더 주지 않는다는 결정이 있다.

### 3) 훅과 API 클라이언트는 건드리지 않는다

`useSystemStatus`에 `enabled` 같은 인자를 추가하지 마라. 컴포넌트 분리로 이미 해결되며, 훅에
스위치를 다는 쪽은 호출부가 늘 때마다 같은 판단을 반복하게 만든다.

`lib/api.ts`의 `request`는 이미 `credentials: "same-origin"`으로 쿠키를 보낸다. 고칠 것이 없다.

## Acceptance Criteria

```bash
cd frontend

# 1) 새 페이지 테스트 + 기존 프론트 테스트 전량
npm run test
#   → 전부 passed. 미로그인 시 status fetch 0건을 단언하는 테스트가 포함되어야 한다

# 2) 가드가 실제로 들어갔다
grep -c "useAuth" src/app/admin/status/page.tsx
#   → 1 이상

# 3) 관리자 권한을 요구하지 않는다
grep -c "is_admin" src/app/admin/status/page.tsx || echo "0건 — 로그인만 요구"
#   → "0건 — 로그인만 요구"

# 4) 훅은 그대로다
git diff --stat src/lib/useSystemStatus.ts src/lib/api.ts src/components/StatusPanel.tsx
#   → 출력 없음 (이 세 파일은 변경되지 않아야 한다)

# 5) 린트·빌드
npm run lint
npm run build
#   → 에러 없음

# 6) 전체 검증
cd .. && bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 미로그인 상태에서 `/api/system/status` 요청이 **한 번도** 나가지 않는가? (테스트가 이것을
     단언해야 한다 — 문구만 바뀌고 폴링이 계속되면 이 step은 실패다)
   - TypeScript strict mode를 위반하지 않았는가? (`any` 금지)
   - `UI_GUIDE.md`의 문구·색 규칙에서 벗어나지 않았는가?
3. **육안 확인은 사람의 몫으로 남긴다.** 개발 서버를 띄워 브라우저로 확인하려 하지 마라 —
   백엔드·프론트·DB를 동시에 띄워야 하고, 이 step의 판정은 위 테스트가 한다.
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **가드를 조기 `return`만으로 처리하지 마라.** 이유: `useSystemStatus()`는 이미 최상단에서
  실행되어 폴링이 시작된 뒤다. 화면 문구만 바뀌고 401 요청은 2초마다 계속 나간다.
- **`useSystemStatus`에 `enabled` 인자를 추가하지 마라.** 이유: 컴포넌트 분리로 해결되며, 훅에
  스위치를 달면 호출부가 늘 때마다 같은 판단을 반복한다.
- **`is_admin`을 요구하지 마라.** 이유: ADR-028이 관리자 권한을 계정 관리 전용으로 한정했다.
  열람 조건을 관리자에게 묶으면 그 결정과 충돌한다.
- **`StatusPanel`·`ErrorDocuments`·`lib/api.ts`를 고치지 마라.** 이유: 이 step의 변경은 페이지
  진입 조건 하나다. 표시 컴포넌트는 이미 옳다.
- **백엔드를 고치지 마라.** 이유: 401은 step 2에서 이미 끝났다.
- **개발 서버를 띄우려 하지 마라.** 이유: DB·API·워커·프론트를 함께 세워야 하고, 판정은 테스트가
  한다.
- **기존 테스트를 깨뜨리지 마라.**
