# Step 2: 프론트엔드 스캐폴드

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — "디렉토리 구조" 절(프론트엔드 트리), "프론트엔드 패턴" 절, "상태 관리" 절
- `/docs/UI_GUIDE.md` — 화면 구성과 UI 규칙
- `/scripts/check.sh` — frontend에 대해 `npm run lint`, `npm run test`, `npm run build`를 **이 순서로** 실행한다
- `/scripts/hooks/tdd-guard.sh` — 프론트엔드 테스트 탐색 경로. `frontend/src/__tests__/<모듈명>.test.tsx`를 인식한다
- **이전 step 산출물**: `/backend/app/main.py` — API가 노출하는 경로 접두사(`/api/...`)를 확인해 rewrites 대상에 반영한다

## 배경

이 step은 프론트엔드의 **뼈대만** 만든다. 실화면(문서 목록·업로드·상세·검색·운영 상태)은 후속 phase 범위다. 목표는 "빌드되고 린트가 통과하고 테스트가 도는 최소 상태"다.

## 작업

### 1. Next.js 스캐폴드 생성

`create-next-app`으로 `frontend/`를 생성한다. 대화형 프롬프트가 뜨지 않도록 플래그를 모두 지정하라.

```bash
npx --yes create-next-app@latest frontend \
  --ts --tailwind --eslint --app --src-dir --import-alias "@/*" --use-npm --yes
```

- **`--src-dir`은 필수다.** `ARCHITECTURE.md`가 `frontend/src/app/` 구조를 명시한다.
- git 초기화를 비활성화하는 플래그(`--disable-git` 등)가 이 버전에서 지원되는지 `npx create-next-app@latest --help`로 확인해 함께 지정하라. 생성 후 `git -C frontend rev-parse --git-dir` 로 **중첩 git 저장소가 생기지 않았는지 반드시 확인**한다. 생성되었다면 제거해야 한다.
- 플래그 이름이 이 버전에서 다르면 `--help` 출력을 근거로 대응되는 플래그를 쓰되, **선택 항목은 위 표의 의도(TypeScript / Tailwind / ESLint / App Router / src 디렉토리 / `@/*` 별칭)를 그대로 유지하라.**
- 생성기가 만든 Tailwind 설정 방식(v3 config 파일이든 v4 CSS-first든)은 **그대로 둔다.** 이유: 생성기가 그 Next 버전에 맞는 조합을 만든다. 임의로 되돌리면 빌드가 깨진다.

### 2. TypeScript strict 확인

`frontend/tsconfig.json`의 `compilerOptions.strict`가 `true`인지 확인한다. 생성기 기본값이 이미 `true`이므로 보통 손댈 것이 없다. `false`라면 `true`로 고친다.

### 3. Vitest 구성

`check.sh`가 `npm run test`를 호출하므로 테스트 러너가 반드시 있어야 한다.

설치:

```
vitest  @vitejs/plugin-react  jsdom  vite-tsconfig-paths
@testing-library/react  @testing-library/jest-dom
```

- `frontend/vitest.config.ts` — `environment: "jsdom"`, React 플러그인, `@/*` 별칭 해석, setup 파일 등록
- setup 파일에서 `@testing-library/jest-dom` matcher를 등록한다
- `package.json`의 `scripts.test`는 **`vitest run`** 으로 둔다 (금지사항 참조)

### 4. API 프록시 (rewrites)

`next.config`에 rewrites를 추가해 `/api/*` 요청을 FastAPI(`http://localhost:8000`)로 넘긴다. `ARCHITECTURE.md`의 "API 연동은 `next.config.js` rewrites로 FastAPI 프록시"에 해당한다.

- 백엔드 주소는 환경변수로 덮어쓸 수 있게 하고, 기본값을 `http://localhost:8000`으로 둔다.

### 5. placeholder 화면과 스모크 테스트

**테스트를 먼저 작성하고 실패를 확인한 뒤 화면을 만들어라** (TDD).

- `frontend/src/__tests__/page.test.tsx` — 루트 페이지를 렌더링해 프로젝트 이름(`OpenArchive`)이 화면에 나타나는지 확인한다. `assert True` 수준의 무의미한 단언은 금지다
- `frontend/src/app/page.tsx` — 프로젝트 이름과 "구현 진행 중" 정도의 한 줄만 있는 placeholder. Tailwind 클래스로 최소한의 레이아웃만 준다

생성기가 만든 기본 랜딩 페이지(Next.js 로고·링크 목록)는 위 placeholder로 교체한다.

### 6. 빌드가 폰트 다운로드로 실패하는 경우

생성기의 `layout.tsx`는 `next/font/google`을 쓴다. 네트워크가 막혀 `npm run build`가 폰트 다운로드에서 실패하면, `next/font` 사용을 걷어내고 CSS의 시스템 폰트 스택으로 대체하라. 그 경우 무엇을 왜 바꿨는지 summary에 적어라.

## Acceptance Criteria

```bash
cd frontend
npm install
npm run lint
npm run test      # 워치 모드로 멈추지 않고 종료되어야 한다
npm run build

cd ..
bash scripts/check.sh     # backend + frontend 전부 통과
```

`bash scripts/check.sh`가 종료 코드 0으로 끝나는 것이 이 phase 전체의 완료 조건이다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `frontend/src/app/` 구조인가? (`--src-dir`)
   - `tsconfig.json`의 `strict`가 `true`인가?
   - `npm run test`가 스스로 종료되는가? (`check.sh`가 Stop 훅에서 실행되므로 워치 모드는 세션을 멈춘다)
   - 중첩 git 저장소(`frontend/.git`)가 없는가?
3. 결과에 따라 `phases/m0-scaffold/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **`scripts.test`를 워치 모드(`vitest`)로 두지 마라. `vitest run`을 써라.** 이유: `check.sh`는 Stop 훅에서 실행되므로 종료되지 않는 프로세스는 세션 전체를 멈춘다.
- **실화면을 만들지 마라** — `/documents/[id]`, `/search`, `/admin/status`, 업로드 드롭존, 검색 폼, 상태 배지 등. 이유: 각각 후속 phase의 범위이며, 지금 만들면 API 계약이 정해지기 전의 추측 코드가 된다.
- **API 클라이언트(`src/lib/api.ts`)를 만들지 마라.** 이유: 호출할 엔드포인트가 `/api/health` 하나뿐이라 추상화할 대상이 없다.
- **전역 상태 라이브러리를 넣지 마라** (zustand, redux, jotai, recoil 등). 이유: `ARCHITECTURE.md` "상태 관리" — 클라이언트 상태는 `useState`/`useReducer`만 쓴다.
- **UI 컴포넌트 라이브러리를 넣지 마라** (shadcn/ui, MUI, Chakra 등). 이유: 스타일링은 Tailwind로만 한다.
- **데이터 페칭 라이브러리를 넣지 마라** (SWR, TanStack Query 등). 이유: 서버 상태는 `fetch` + 2초 폴링으로 처리하기로 되어 있다. SSE/웹소켓도 쓰지 않는다.
- **빈 디렉토리를 `.gitkeep`으로 채워 만들지 마라** (`components/`, `types/`, `lib/`). 이유: 실제 파일이 생길 때 만든다. 빈 디렉토리는 구조를 설명하지 않는다.
- **`frontend/`를 별도 git 저장소로 만들지 마라.** 이유: 중첩 저장소가 생기면 하네스의 커밋이 프론트엔드 파일을 누락한다.
- 기존 테스트를 깨뜨리지 마라. backend 쪽 `pytest`는 계속 통과해야 한다.
