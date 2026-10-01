# Step 8: access-panel

부여 대상 선택 컴포넌트 `GranteePicker`를 만들고 두 곳에 쓴다: ① 문서 상세의 「열람 범위」 패널(소유자
전용) ② 업로드에서 「제한」을 고르면 나오는 대상 선택.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 디자인 규칙, step 0이 추가한 열람 범위 패널·업로드 대상 선택 설명
- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」 결정 2·4·5
- `frontend/src/app/documents/[id]/DocumentDetailView.tsx` — 패널을 둘 자리, `useAuth`(현재 사용자명), `anonymous` 분기
- `frontend/src/components/TagEditor.tsx`·`.test.tsx` — 목록 편집·저장·오류 표시 선례
- `frontend/src/components/UploadDropzone.tsx`·`.test.tsx` — step 6이 바꾼 「열람 범위」 라디오
- `frontend/src/lib/api.ts` — `listPrincipals`, `getDocumentAccess`, `setDocumentAccess`, `uploadDocument`의 `grantUsers`·`grantGroups`
- `frontend/src/lib/types.ts` — `DocumentAccess`, `Principals`, 공개범위 문구 상수

## 작업

### 1) 테스트 먼저

`frontend/src/components/GranteePicker.test.tsx`:
1. `principals`(사용자·그룹 이름)에서 대상을 골라 추가하고, 선택된 대상을 제거할 수 있다. 이미 고른 대상은 후보에서 빠진다. 사용자와 그룹이 구분되어 보인다.

`frontend/src/components/AccessPanel.test.tsx`:
2. 소유자에게만 렌더된다(비소유자·익명 → 아무것도 렌더하지 않음 — 부모가 판단해 넘기는 방식이면 부모 테스트로).
3. 현재 범위(`getDocumentAccess`)를 보여 준다: 조직 공개면 대상 선택이 없고, 제한이면 대상 목록이 보인다.
4. 「제한」 + 대상 선택 → 저장 → `setDocumentAccess(id, {visibility: "private", users, groups})`. 「조직 공개」로 저장하면 `users: [], groups: []`로 보낸다(부여가 함께 사라진다는 안내 문구가 있다).
5. 서버 오류(400·403)는 detail을 보여 주고 편집 중이던 선택을 지우지 않는다.
6. API 토큰이 아닌 세션 화면이므로 별도 처리는 없지만, 403 detail("로그인 세션이 필요합니다." 등)도 5와 같이 보인다.

`UploadDropzone.test.tsx`:
7. 「제한」을 고르면 `GranteePicker`가 나타나고, 고른 대상이 `uploadDocument`의 `grantUsers`·`grantGroups`로 간다. 「조직 공개」로 돌리면 선택이 비워지고 대상 인자를 보내지 않는다.

`frontend/src/app/documents/[id]/page.test.tsx`:
8. 소유자에게 열람 범위 패널이 보이고 다른 사용자에게는 보이지 않는다. 범위를 저장하면 문서 메타의 공개범위 표시가 갱신된다(`refresh`).

### 2) 구현

- `frontend/src/components/GranteePicker.tsx`
  ```ts
  interface Props {
    principals: Principals;
    users: string[]; groups: string[];
    onChange(next: { users: string[]; groups: string[] }): void;
    disabled?: boolean;
  }
  ```
- `frontend/src/components/AccessPanel.tsx` — `documentId`, `onSaved` props. 마운트 시 `getDocumentAccess`·`listPrincipals`를 부른다(AbortController — 기존 컴포넌트의 화면 이탈 취소 방식을 따른다).
- `DocumentDetailView.tsx` — `auth.username === document.owner_id`일 때만 `AccessPanel`을 둔다. 편집 중(`editing`)에는 다른 액션처럼 비활성화한다.
- `UploadDropzone.tsx` — 「제한」 선택 시 `listPrincipals`를 불러 `GranteePicker`를 보인다.

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. `check.sh`는 오래 걸릴 수 있다(600초 초과 시 백그라운드로 돌리고 결과를 기다린다). 실행하지 못했으면 이유를 summary에 적는다 — 통과로 처리하지 마라.
2. 체크리스트: 비소유자에게 부여 대상 목록이 화면에 나오지 않는가(GET /access는 비소유자에게 403이지만, 호출 자체를 하지 않아야 한다)? 문구에 "항상 최신"·"실시간"이 없는가?
3. `phases/m20-grants-admin/index.json`의 step 8을 갱신한다. summary에 컴포넌트 이름과 check.sh 결과(backend·frontend 테스트 수)를 적는다.

## 금지사항

- 비소유자 화면에서 `getDocumentAccess`를 호출하지 마라. 이유: 403 오류가 화면에 뜨고, 패널 자리가 "이 문서에 제한이 있다"를 드러낸다.
- 업로드에서 「조직 공개」인데 대상 인자를 보내지 마라. 이유: 서버가 400으로 거부한다(ADR-044 관리 경로 결정 4).
- 저장 실패 시 선택을 초기화하지 마라. 이유: TagEditor·TextEditor와 같은 원칙 — 사용자의 편집을 잃지 않는다.
- 백엔드를 고치지 마라.
- 기존 테스트를 깨뜨리지 마라
