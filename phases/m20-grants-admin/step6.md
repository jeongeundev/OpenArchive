# Step 6: frontend-client

프론트엔드의 API 클라이언트·타입에 그룹·부여 대상·열람 범위를 추가하고, 공개범위 표시 문구를
**「조직 공개 / 제한」**으로 바꾼다. 새 화면은 step 7·8이다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — step 0이 고친 공개범위 문구와 `/admin/groups`·열람 범위 패널 설명
- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」(API 형태)
- `backend/openarchive/api/groups.py`, `backend/openarchive/api/documents.py`, `backend/openarchive/api/schemas.py` — step 3·4의 실제 경로·스키마(`GroupSummary`, `Principals`, `DocumentAccess`, `UpdateAccessRequest`, 업로드 Form `grant_users`·`grant_groups`)
- `frontend/src/lib/api.ts` — `listUsers`·`createUser`·`deleteUser`(관리자 API 선례), `uploadDocument`(FormData에 `tags`를 반복 append하는 방식), `updateTags`, `request` 옵션 객체·재시도 규칙(`isRetryable`이 읽기 경로만 재시도한다)
- `frontend/src/lib/types.ts` — `Visibility`, `UserSummary`
- `frontend/src/lib/api.test.ts`
- `frontend/src/components/DocumentMeta.tsx`·`DocumentTable.tsx`·`UploadDropzone.tsx`와 각 `.test.tsx`

## 작업

### 1) 테스트 먼저

- `api.test.ts`: 새 함수마다 메서드·경로·본문을 검증한다. `uploadDocument`에 대상을 주면 FormData에 `grant_users`·`grant_groups`가 항목마다 append되고, 주지 않으면 그 키가 없다.
- `DocumentMeta.test.tsx`·`DocumentTable.test.tsx`: `public` → 「조직 공개」, `private` → 「제한」.
- `UploadDropzone.test.tsx`: 라디오 라벨이 「조직 공개」·「제한」이고 보내는 값은 여전히 `public`/`private`다.

### 2) 구현

`types.ts`:
```ts
export interface GroupSummary { id: string; name: string; created_at: string; members: string[] }
export interface Principals { users: string[]; groups: string[] }
export interface DocumentAccess { visibility: Visibility; users: string[]; groups: string[] }
```

`api.ts`:
```ts
listGroups(signal?: AbortSignal): Promise<GroupSummary[]>
createGroup(name: string): Promise<GroupSummary>
deleteGroup(id: string): Promise<void>
addGroupMember(groupId: string, username: string): Promise<void>     // PUT …/members/{username}
removeGroupMember(groupId: string, username: string): Promise<void>  // DELETE …/members/{username}
listPrincipals(signal?: AbortSignal): Promise<Principals>
getDocumentAccess(id: string, signal?: AbortSignal): Promise<DocumentAccess>
setDocumentAccess(id: string, access: DocumentAccess): Promise<DocumentAccess>
uploadDocument({ ..., grantUsers?: string[], grantGroups?: string[] })
```
- 경로의 사용자명·id는 `encodeURIComponent`로 감싼다(한글 그룹명·사용자명).
- 공개범위 문구는 한 곳(예: `types.ts`의 `VISIBILITY_LABEL: Record<Visibility, string>`)에 두고 세 컴포넌트가 그것을 쓴다.
- `UploadDropzone`의 legend 「공개범위」는 「열람 범위」로 바꾼다. 대상 선택 UI는 step 8이다 — 이 step에서는 문구만.

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
```

## 검증 절차

1. 위 AC 커맨드를 실행한다(`build:static`은 `backend/openarchive/static`도 갱신한다 — 그 변경도 산출물이다).
2. 체크리스트: TypeScript strict에서 `any`를 쓰지 않았는가? 값 `public`/`private`를 바꾸지 않았는가(PyPI 0.1.0 계약)?
3. `phases/m20-grants-admin/index.json`의 step 6을 갱신한다. summary에 함수·타입 이름과 문구 상수 위치를 적는다.

## 금지사항

- `Visibility` 값(`"public" | "private"`)을 바꾸지 마라. 이유: 서버 계약이다 — 문구만 바꾼다.
- 쓰기 요청(`setDocumentAccess`·그룹 변경)을 재시도 대상에 넣지 마라. 이유: 기존 `isRetryable` 규칙이 읽기 경로만 재시도한다(#119).
- 새 화면·컴포넌트를 만들지 마라. 이유: step 7·8의 범위다.
- 백엔드를 고치지 마라.
- 기존 테스트를 깨뜨리지 마라
