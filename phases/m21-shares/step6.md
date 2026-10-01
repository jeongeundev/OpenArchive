# Step 6: frontend-client

공유 관리 API(step 4)의 타입과 클라이언트 함수를 둔다. 화면은 step 7·8이다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — `/settings` 「외부 공유」 절, 문서 상세 「열람 범위」 패널의 「내 공유에 포함」(step 0이 기록)
- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 API 형태 표
- `backend/openarchive/api/shares.py`, `backend/openarchive/api/schemas.py`(`ShareSummary` 등) — step 4의 실제 응답 형태
- `frontend/src/lib/api.ts` — `listTokens`·`createToken`·`revokeToken`(토큰 발급 선례), `listGroups`·`addGroupMember`·`removeGroupMember`(PUT/DELETE 204, `encodeURIComponent`), `setDocumentAccess`(재시도하지 않는 쓰기)
- `frontend/src/lib/types.ts`, `frontend/src/lib/api.test.ts`

## 작업

### 1) 테스트 먼저 — `frontend/src/lib/api.test.ts`

1. `listShares(signal?)` → `GET /api/shares`, `signal` 전달.
2. `createShare(name)` → `POST /api/shares` JSON `{name}`.
3. `deleteShare(id)` → `DELETE /api/shares/{id}`.
4. `addShareDocument(shareId, documentId)` / `removeShareDocument(shareId, documentId)` → `PUT`/`DELETE /api/shares/{shareId}/documents/{documentId}`, 경로 조각은 `encodeURIComponent`.
5. `createShareToken(shareId, name)` → `POST /api/shares/{shareId}/tokens` JSON `{name}`, 응답의 `token` 원문을 그대로 돌려준다.
6. `revokeShareToken(shareId, tokenId)` → `DELETE /api/shares/{shareId}/tokens/{tokenId}`.
7. 4xx 응답의 `detail`이 `ApiError`로 전달된다(기존 헬퍼 동작 — 한 함수로 확인).

### 2) 구현

- `types.ts`: `ShareDocument { id; title }`, `ShareTokenSummary { id; name; scope: "read"; created_at }`, `ShareSummary { id; name; created_at; documents: ShareDocument[]; tokens: ShareTokenSummary[] }`, `ShareTokenCreated`(= `ShareTokenSummary & { token: string }`). 실제 백엔드 스키마와 필드명을 맞춘다.
- `api.ts`: 위 7개 함수. 쓰기(POST/PUT/DELETE)는 기존 쓰기 함수와 같은 재시도 정책을 따른다(재시도 대상 경로 목록이 있으면 넣지 않는다 — 기존 `setDocumentAccess`·그룹 함수와 같게).

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 백엔드 스키마와 타입 필드명을 한 번 더 대조한다(`ShareSummary`·토큰 응답).
3. `phases/m21-shares/index.json`의 step 6을 갱신한다.

## 금지사항

- 화면·컴포넌트를 만들지 마라. 이유: step 7·8의 범위다.
- 백엔드를 고치지 마라.
- 공유 토큰 원문을 저장(localStorage 등)하지 마라. 이유: ADR-034 — 원문은 발급 응답에서 한 번만 보인다.
- 기존 테스트를 깨뜨리지 마라
