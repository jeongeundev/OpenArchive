# Step 7: settings-shares

`/settings`에 「외부 공유」 절을 둔다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — `/settings` 「외부 공유」 절(step 0), 문구 규칙
- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」(공유는 조직 공개 문서도 외부에 연다, 공유 토큰은 읽기 전용, 원문 1회)
- `frontend/src/app/settings/page.tsx`, `page.test.tsx` — 「API 토큰」 절(발급 → 원문 1회 표시 → 목록·폐기)과 「비밀번호」 절의 형식
- `frontend/src/app/admin/groups/` — 목록·생성·확인 뒤 삭제의 선례
- `frontend/src/lib/api.ts`, `types.ts` — step 6의 공유 함수

## 작업

### 1) 테스트 먼저 — `frontend/src/app/settings/page.test.tsx`(또는 절을 컴포넌트로 빼면 그 테스트)

1. 「외부 공유」 절이 내 공유 목록(이름, 포함 문서 제목 — 상세로 가는 링크, 토큰 이름)을 보인다. 공유가 없으면 빈 상태 안내.
2. 이름을 입력해 만들면 `createShare`가 불리고 목록에 나타난다. 409 등 `ApiError.detail`이 그대로 보인다.
3. 포함 문서의 「빼기」 → `removeShareDocument` 후 목록에서 사라진다.
4. 토큰 발급 → 원문이 **한 번** 보이고(복사 안내), 목록에는 이름만 남는다. 다른 동작 후 원문이 사라진다. 폐기 → `revokeShareToken` 후 사라진다.
5. 공유 삭제는 확인을 거친다 — 확인 문구가 "이 공유의 토큰이 모두 무효가 되고 외부에서 더 이상 볼 수 없다"는 뜻을 담는다. 취소하면 `deleteShare`가 불리지 않는다.
6. 절 안내 문구가 ① 공유 토큰은 읽기 전용이고 공유에 넣은 문서만 보인다 ② 조직 공개 문서도 공유에 넣으면 외부에 열린다 ③ 문서는 문서 상세의 열람 범위 패널에서 넣는다, 를 알린다.
7. 목록 조회 실패는 빈 목록으로 숨기지 않고 오류를 보인다.

### 2) 구현

- 절이 커지면 `frontend/src/components/SharesSection.tsx`로 분리한다(분리하면 테스트도 그 파일로).
- 조회는 `AbortController`로 화면 이탈 시 취소한다(기존 절과 같게).
- 문구에 "실시간"·"항상 최신"을 쓰지 않는다(CLAUDE.md).

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 삭제 확인을 건너뛰게 바꾸면 테스트 5가 실패하는가? 원문을 계속 보이게 하면 테스트 4가 실패하는가? 통과하면 보강한다.
3. `phases/m21-shares/index.json`의 step 7을 갱신한다.

## 금지사항

- 문서를 공유에 넣는 UI를 이 절에 만들지 마라. 이유: 사용자 결정 — 넣기는 문서 상세(step 8)에서 한다.
- 설정 화면의 기존 절(API 토큰·비밀번호)의 동작을 바꾸지 마라.
- 백엔드를 고치지 마라.
- 기존 테스트를 깨뜨리지 마라
