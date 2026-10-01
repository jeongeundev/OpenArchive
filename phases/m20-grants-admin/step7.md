# Step 7: admin-groups-page

관리자 그룹 관리 화면 `/admin/groups`를 둔다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 디자인 규칙 전체, step 0이 추가한 `/admin/groups` 항목
- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」 결정 1(그룹 부여는 관리자를 신뢰한다)
- `frontend/src/app/admin/users/page.tsx`·`page.test.tsx` — **이 화면의 형식을 그대로 따른다**(관리자 가드 방식, 목록·생성·삭제, 오류 표시, 확인 절차)
- `frontend/src/components/SiteHeader.tsx`·`SiteHeader.test.tsx` — 관리자에게만 보이는 `/admin/users` 링크
- `frontend/src/components/RequireAuth.tsx` — 가드가 이미 하는 일(중복 가드를 만들지 않기 위해 — m11-a·#78에서 RequireAuth와 중복된 도달 불가 가드가 두 번 결함으로 잡혔다)
- `frontend/src/lib/api.ts` — step 6의 `listGroups`·`createGroup`·`deleteGroup`·`addGroupMember`·`removeGroupMember`, `listUsers`

## 작업

### 1) 테스트 먼저 — `frontend/src/app/admin/groups/page.test.tsx`

1. 그룹 목록이 이름과 구성원(사용자명)으로 보인다. 그룹이 없으면 빈 상태 문구.
2. 이름을 넣고 생성 → `createGroup` 호출 후 목록 갱신. 409면 서버 detail을 보여 준다.
3. 구성원 추가: 사용자 선택(`listUsers` 결과 중 아직 구성원이 아닌 사용자) → `addGroupMember`. 구성원 제거 → `removeGroupMember`.
4. 그룹 삭제는 확인 단계를 거친다. 확인 문구가 "이 그룹에 부여된 문서는 구성원에게 더 이상 보이지 않습니다"를 말한다.
5. 화면 상단 안내에 "그룹 구성원을 바꾸면 그 그룹에 부여된 문서의 열람이 바뀝니다. 관리자도 보면 안 되는 문서는 사용자에게 직접 부여하세요." 취지의 문구가 있다.
6. `SiteHeader.test.tsx`: 관리자에게 「그룹」 링크(`/admin/groups`)가 보이고, 일반 사용자에게는 안 보인다.

### 2) 구현

- `frontend/src/app/admin/groups/page.tsx` — `/admin/users/page.tsx`와 같은 구조.
- `SiteHeader.tsx` — `/admin/users` 링크 옆에 같은 조건으로 링크 추가.

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: `/admin/users`와 같은 가드를 쓰는가(새 가드를 덧대지 않았는가)? UI_GUIDE 색·간격 규칙을 따르는가?
3. `phases/m20-grants-admin/index.json`의 step 7을 갱신한다.

## 금지사항

- RequireAuth가 이미 막는 상태를 페이지 안에서 다시 분기하지 마라. 이유: 도달 불가 가드가 두 번 결함으로 잡혔다(m11-a, #78).
- 그룹 이름 변경 UI를 만들지 마라. 이유: 이름 변경 API가 없다(이름이 부여 지정 계약이다).
- 그룹 화면에 그 그룹에 부여된 문서 목록을 보여 주지 마라. 이유: 관리자에게 문서 존재가 샌다(ADR-027) — API도 그런 경로가 없다.
- 백엔드를 고치지 마라.
- 기존 테스트를 깨뜨리지 마라
