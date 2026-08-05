# Step 1: layout-shell

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — **이 step의 기준 문서다.** 디자인 원칙 4개, AI 슬롭 안티패턴 표, 색상표, 컴포넌트 클래스, 레이아웃, 타이포그래피, 애니메이션
- `/docs/ARCHITECTURE.md` — "프론트엔드 패턴" 절
- `/docs/PRD.md` — MVP 제외 사항(실제 인증 없음)
- `frontend/src/lib/user.ts` — step 0에서 만든 데모 사용자 저장소
- `frontend/src/lib/types.ts` — step 0에서 만든 타입(`EmbeddingStatus` 등)
- `frontend/src/app/layout.tsx`, `frontend/src/app/globals.css` — 현재 스캐폴드 상태
- `frontend/node_modules/next/dist/docs/01-app/` — **이 저장소의 Next는 16.3.0이다.** App Router 규약이 학습 데이터와 다를 수 있으니 파일 컨벤션을 직접 확인하라 (`frontend/AGENTS.md` 지시)

## 작업

네 화면이 공유할 껍데기를 만든다. **개별 화면은 만들지 않는다.**

### 1. `frontend/src/components/StatusBadge.tsx` + `StatusBadge.test.tsx`

```tsx
export function StatusBadge({ status }: { status: EmbeddingStatus }): React.ReactElement;
```

- 클래스 골격은 `UI_GUIDE.md` 상태 배지 규칙을 따른다: `rounded px-2 py-0.5 text-xs font-medium` + 시맨틱 색 텍스트 + 같은 색 10% 배경
- 색: `ready` → `#22c55e`, `error` → `#ef4444`, `pending`·`processing` → `#a3a3a3`. **이 세 가지 외의 색을 쓰지 마라**
- 라벨은 한국어로: `pending` "대기 중", `processing` "처리 중…", `ready` "완료", `error` "실패"
- **`processing`은 색이 아니라 텍스트로 진행을 표현한다**(UI_GUIDE 색상 절). `pending`과 같은 회색을 쓰고 라벨만 다르다
- Tailwind는 클래스 문자열을 정적으로 수집한다. `` `text-[${color}]` `` 같은 템플릿 조립은 스타일이 빌드에서 누락되므로, **상태 → 완전한 클래스 문자열** 맵을 두고 통째로 고른다

테스트: 네 상태 각각의 라벨이 렌더된다 / `ready`와 `error`의 클래스가 서로 다르다 / `pending`과 `processing`은 라벨이 다르다.

### 2. `frontend/src/components/UserSwitcher.tsx` + `UserSwitcher.test.tsx`

데모 사용자를 전환하는 헤더 위젯이다. **실제 로그인이 아니며 그렇게 보이게 만들지 마라** — 자물쇠 아이콘, "로그인/로그아웃" 문구, 아바타를 쓰지 않는다.

```tsx
export function UserSwitcher(): React.ReactElement;
```

- Client Component(`"use client"`)
- 선택지는 `DEMO_USERS` + **익명**. 익명 항목의 라벨은 "익명(공개 문서만)"
- 마운트 후 `getCurrentUser()`를 읽어 현재 값을 반영한다. **서버 렌더 시점에는 `localStorage`를 읽을 수 없으므로** 초기 렌더와 마운트 후 값이 다를 수 있다 — `useEffect`에서 읽어 상태에 넣어라(hydration 불일치를 만들지 마라)
- 변경 시 `setCurrentUser(...)` 후 **`window.location.reload()`로 화면 전체를 다시 그린다.** 이유: 사용자 변경은 목록·검색·상세의 모든 요청 결과를 바꾸는데, 전역 상태 라이브러리 없이(ARCHITECTURE "상태 관리") 화면 간에 전파할 방법이 없다. 데모에서 드물게 일어나는 조작이라 reload가 가장 단순하고 확실하다
- 요소는 네이티브 `<select>`를 쓴다. 입력 필드 클래스(`UI_GUIDE.md` 컴포넌트 절)를 기준으로 하되 헤더에 맞게 패딩을 줄여도 된다

테스트에서 `window.location.reload`는 스텁한다. 검증할 것: 옵션에 익명과 프리셋이 모두 있다 / 선택 시 `setCurrentUser`가 그 값으로 불린다 / 익명 선택 시 `null`로 불린다.

### 3. `frontend/src/components/SiteHeader.tsx` + `SiteHeader.test.tsx`

```tsx
export function SiteHeader(): React.ReactElement;
```

- 좌측: 제품명 "OpenArchive"(`/`로 가는 링크), 그 옆에 내비게이션 링크 **2개만** — "문서"(`/`), "검색"(`/search`)
- 우측: `<UserSwitcher />`
- **`/admin/status` 링크를 넣지 마라.** 이유: 운영 화면은 사용자 내비게이션에 노출하지 않는다(UI_GUIDE 운영 화면 절). URL 직접 입력으로만 접근한다
- **접속 노드·페일오버·DB 상태를 헤더에 표시하지 마라.** 이유: 사용자 화면은 인프라를 드러내지 않는다(UI_GUIDE 디자인 원칙 3). 상단 고정 상태바도 만들지 않는다(레이아웃 절)
- 링크는 `next/link`를 쓴다

테스트: 문서·검색 링크가 각각 `/`, `/search`를 가리킨다 / **`/admin` 문자열이 렌더 결과에 없다**.

### 4. `frontend/src/app/layout.tsx` 수정

- `<SiteHeader />`를 `<body>` 최상단에 두고 그 아래 `{children}`
- 본문 컨테이너는 `max-w-5xl`, 좌측 정렬(UI_GUIDE 레이아웃 절). 중앙 정렬 유틸리티로 콘텐츠를 가운데 모으지 마라 — `mx-auto`로 컨테이너 자체를 화면 중앙에 두는 것은 무방하다
- 기존 `LayoutProps<"/">` 타입 사용과 `lang="ko"`, 폰트 변수 설정은 유지한다

### 5. `frontend/src/app/globals.css` 수정

- `body`의 `font-family: Arial, Helvetica, sans-serif;`를 **Geist 변수(`--font-sans`)를 쓰도록 고친다.** 이유: `layout.tsx`가 Geist를 로드해 CSS 변수로 노출하는데 본문이 Arial로 덮여 폰트가 적용되지 않는다. 스캐폴드 잔재이며 이 step에서 헤더를 넣는 순간 눈에 띈다
- 카드 배경(`#141414`) 등 UI_GUIDE 색을 CSS 변수로 추가해도 되지만, **필수는 아니다.** Tailwind 임의값(`bg-[#141414]`)으로 충분하다면 변수를 늘리지 마라

## Acceptance Criteria

```bash
cd frontend
npm run lint
npm run test
npm run build
```

UI 슬롭 안티패턴 검사(매치가 있으면 실패):

```bash
cd frontend && ! grep -rEn "backdrop-blur|backdrop-filter|bg-gradient|blur-3xl|purple-|indigo-|violet-|animate-(pulse|bounce|spin|ping)" src/
```

내비게이션에 운영 화면이 새지 않았는지 확인:

```bash
cd frontend && ! grep -rn "/admin" src/components/
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `UI_GUIDE.md` 색상표에 없는 색을 쓰지 않았는가? (포인트 색은 `#0ea5e9` 하나뿐이다)
   - 상태 배지가 시맨틱 색 3종만 쓰는가?
   - 헤더가 인프라 정보를 노출하지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (다음 step이 재사용할 컴포넌트 이름과 경로를 담아라)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **화면(`page.tsx`)을 만들지 마라.** 이 step은 레이아웃 껍데기와 공용 컴포넌트 3개까지다. 이유: 목록·상세·검색·운영 화면은 step 2~7이 각각 담당한다
- **`UI_GUIDE.md`의 AI 슬롭 안티패턴 표에 있는 것을 쓰지 마라.** glass morphism(`backdrop-filter`), gradient text, "Powered by AI" 배지, 글로우 애니메이션, 보라/인디고 브랜드 색, 모든 카드에 동일한 `rounded-2xl`, 배경 gradient orb. 이유: AI가 만든 템플릿처럼 보이는 것이 이 프로젝트에서 가장 피해야 할 인상이다
- **fade-in(0.4s) 외의 애니메이션을 넣지 마라.** 이유: UI_GUIDE 애니메이션 절이 그 외 전부를 금지한다
- **로그인·회원가입·권한 관리 UI를 만들지 마라.** 이유: 실제 인증은 MVP 제외 사항이며(PRD), `X-User-Id`는 권한 필터를 시연하기 위한 데모 장치다
- **아이콘을 둥근 배경 박스로 감싸지 마라.** 아이콘이 필요하면 인라인 SVG에 `strokeWidth 1.5`를 쓴다(UI_GUIDE 아이콘 절)
- 기존 테스트를 깨뜨리지 마라 — `src/__tests__/page.test.tsx`는 아직 스캐폴드 페이지를 검증한다. 이 step에서 `app/page.tsx`를 건드리지 않으면 그대로 통과한다
