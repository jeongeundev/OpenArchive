# Step 7: admin-status

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/UI_GUIDE.md` — **"운영 화면(1화면, 데모·운영 전용)" 절**, 디자인 원칙 3·4(관측은 별도 채널), 색상표
- `/docs/ARCHITECTURE.md` — "정합성 보장" 절, "API 설계" 절의 `GET /api/system/status`, "고가용성(HA) 전략" 절
- `/docs/ADR.md` — **ADR-020**(Single 구성에서 무엇을 시연할 수 있고 없는가), ADR-015(보장 범위), ADR-009(폴링이 주 경로)
- `/docs/PROJECT_CONTEXT.md` — 이 과제가 요구하는 "장애 상황에서도 원본과 벡터의 정합성 유지"
- `frontend/src/lib/api.ts`, `types.ts` — step 0의 `getSystemStatus`·`listDocuments`·`reembedDocument`, `SystemStatus`
- `frontend/src/lib/useDocuments.ts` — step 2의 폴링 훅(같은 패턴을 따른다)
- `frontend/src/components/StatusBadge.tsx`, `SiteHeader.tsx` — step 1 산출물
- `backend/app/api/system.py` — 상태 응답을 만드는 실제 SQL

## 배경 — 정합성 카운터가 이 프로젝트의 증거다

`/api/system/status`의 `inconsistent_documents`는 다음 쿼리 결과다:

```sql
SELECT count(DISTINCT c.document_id)
FROM document_chunks c JOIN documents d ON d.id = c.document_id
WHERE c.version <> d.version      -- 원본 버전과 청크 버전이 어긋난 문서
```

평상시 **0**이고, 문서를 편집하면 잠깐 **1**로 올랐다가 워커가 재임베딩을 끝내면 다시 **0**으로 수렴한다. 장애를 일으켜도 결국 0으로 돌아온다 — 이것이 "원본-벡터 정합성이 DB 안에서 보장된다"를 숫자로 보여주는 유일한 지표다(UI_GUIDE 운영 화면 절).

이 화면은 **사용자 내비게이션에 노출하지 않는다.** URL 직접 입력으로만 접근하며, 데모에서는 사용자 화면과 나란히 띄워 "장애가 있었고, 복구됐고, 사용자는 몰랐다"를 보여준다.

## 백엔드 계약

`GET /api/system/status` → `SystemStatus`

```
node_address: string | null      // inet_server_addr(). 유닉스 소켓 접속이면 null
node_port: number
jobs: { pending, processing, error }
inconsistent_documents: number
embedding_provider: string
reconnect_events: null           // 항상 null — M5에서 채운다
```

실패 문서 목록은 별도 요청이다: `GET /api/documents?status=error` → `DocumentSummary[]`.

**이 목록에도 권한 필터가 걸린다.** 익명이면 public 문서만, 사용자를 선택하면 public + 그 사용자의 private만 보인다. 관리자 전용 조회 경로는 없다 — 데모 범위의 한계이며 화면에 그 사실을 한 줄로 밝힌다.

재임베딩(`POST /api/documents/{id}/reembed`)은 **소유자만** 가능하다. 타인의 public 문서면 403이 정상이다.

## 작업

### 1. `frontend/src/lib/useSystemStatus.ts` + `useSystemStatus.test.ts`

```ts
export function useSystemStatus(intervalMs?: number): {
  status: SystemStatus | null;
  loading: boolean;
  error: string | null;
};
```

- 마운트 시 1회 + 2초 주기 폴링(기본 2000). 언마운트 시 타이머 정리
- **실패해도 마지막 성공 값을 유지하고 `error`만 채운다.** 이유: DB를 정지시키는 복구 데모에서 이 화면은 요청이 실패하는 구간을 지나 다시 성공해야 한다. 화면이 비면 "복구됐다"를 보여줄 수 없다
- 실패 중임을 화면이 알 수 있어야 한다 — 운영 화면에서는 오류를 숨기지 않는다(사용자 화면과 반대다)

테스트(fake timers + fetch 스텁): 2초 후 재조회한다 / 언마운트 후 호출되지 않는다 / 두 번째 호출 실패 시 이전 값이 남고 `error`가 채워진다.

### 2. `frontend/src/components/StatusPanel.tsx` + `StatusPanel.test.tsx`

```tsx
export function StatusPanel({
  status, error,
}: { status: SystemStatus | null; error: string | null }): React.ReactElement;
```

표시 항목:

| 항목 | 표시 규칙 |
|---|---|
| 접속 DB 노드 | `node_address`가 `null`이면 "유닉스 소켓" 같은 대체 표기 + `:node_port` |
| 임베딩 잡 | `pending` / `processing` / `error` 세 숫자 |
| **정합성 검증** | `inconsistent_documents`를 **가장 크게** 보여준다. 0이면 `#22c55e`, 1 이상이면 `#a3a3a3` |
| 임베딩 프로바이더 | `embedding_provider` 그대로 |
| 재연결 이벤트 | `reconnect_events`가 `null`이므로 "아직 수집하지 않습니다." |

- 정합성 카운터 옆에 한 줄 설명을 둔다: "원본 버전과 청크 버전이 어긋난 문서 수. 재임베딩 중에만 증가했다가 0으로 돌아옵니다."
- **1 이상을 오류로 표시하지 마라**(빨간색 금지). 재임베딩 중에는 정상적으로 올라가는 값이다. 빨간색(`#ef4444`)은 `jobs.error`와 요청 실패에만 쓴다
- `error`가 있으면 패널 상단에 "상태 조회 실패: {메시지}"를 표시하되, **마지막으로 조회된 값은 그대로 남긴다**(언제 기준 값인지 알 수 있게)
- **"무중단"·"failover 시연"이라고 쓰지 마라.** 대회 지시로 Single 구성이라 승격할 replica가 없다(ADR-020). 정확한 표현은 "짧은 중단 후 자동 복구"다

테스트: 정합성 카운터 0과 2의 표시가 다르다 / `node_address`가 null일 때 대체 표기가 나온다 / `error`가 있어도 이전 값이 남는다.

### 3. `frontend/src/components/ErrorDocuments.tsx` + `ErrorDocuments.test.tsx`

```tsx
export function ErrorDocuments(): React.ReactElement;
```

- `listDocuments({ status: "error" })`로 실패 문서를 가져온다. step 2의 `useDocuments({ status: "error" })`를 재사용해도 좋다
- 각 행: 제목(상세 링크), 소유자, 수정일, **재임베딩 버튼**
- 재임베딩 성공 → 목록 갱신. 실패(403·400) → `ApiError.detail`을 그대로 표시한다
- 익명이면 버튼을 비활성화하고 "사용자를 선택하면 재임베딩을 요청할 수 있습니다."
- 빈 목록: "실패한 문서가 없습니다." (오류로 보이지 않게 `text-neutral-500`)
- 목록 아래에 한 줄로 한계를 밝힌다: "권한 필터가 적용되어 현재 선택된 사용자가 볼 수 있는 문서만 표시됩니다."

테스트: 실패 문서가 렌더되고 버튼 클릭 시 `POST /reembed`가 불린다 / 403의 detail이 표시된다 / 익명이면 버튼이 비활성화된다 / 빈 목록 문구.

### 4. `frontend/src/app/admin/status/page.tsx`

- `"use client"`
- `useSystemStatus()` + `<StatusPanel />` + `<ErrorDocuments />` 조립
- 페이지 제목에 이 화면이 운영·데모 전용임을 밝힌다
- **`SiteHeader`에 이 화면 링크를 추가하지 마라**(step 1에서 이미 제외했다). URL 직접 접근이 유일한 경로다
- **page.tsx에는 조립만 둔다**(`tdd-guard.sh`가 `page.tsx`를 테스트 없이 통과시킨다)

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

용어·과장 문구 검사(매치가 있으면 실패 — ADR-015, ADR-017, ADR-018, ADR-020):

```bash
cd frontend && ! grep -rn "항상 최신\|실시간 동기화\|무중단\|원문\|문서 버전 이력\|중복입니다\|일치율\|failover 시연\|페일오버 시연" src/
```

운영 화면이 사용자 내비게이션에 새지 않았는지 확인:

```bash
cd frontend && ! grep -rn "/admin" src/components/SiteHeader.tsx
```

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 정합성 카운터가 가장 눈에 띄는 값인가? 1 이상을 오류로 칠하지 않는가?
   - 폴링 실패 시 마지막 값이 남고 실패 사실도 보이는가?
   - 사용자 내비게이션에 이 화면이 노출되지 않는가?
   - Single 구성에서 할 수 없는 것(리더 선출·승격)을 했다고 쓰지 않는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m3-frontend/index.json`의 step 7을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **접속 노드·잡 수 같은 운영 정보를 사용자 화면(`/`, `/documents/[id]`, `/search`)에 넣지 마라.** 이유: 고가용성의 가치 명제는 "사용자가 장애를 인지하지 않는다"이며, 사용자 화면에 노드 정보를 띄우는 것은 그 명제와 모순된다(UI_GUIDE 디자인 원칙 3)
- **`SiteHeader`나 어떤 사용자 화면에도 `/admin/status` 링크를 추가하지 마라**
- **"무중단", "failover를 시연했다"고 쓰지 마라.** 사무국 지시로 Single 구성이라 승격할 replica가 없다. 시연 가능한 것은 연결 끊김 후 재연결·잡 무손실 재개·좀비 회수·정합성 수렴이다(ADR-020)
- **`embedding_jobs`를 직접 조작하는 요청을 만들지 마라.** 재임베딩은 `POST /api/documents/{id}/reembed` 하나뿐이다(CLAUDE.md CRITICAL)
- **워커를 제어하는 UI(중지·재시작·잡 삭제)를 만들지 마라.** 요청받지 않은 기능이며 백엔드에 해당 API가 없다
- **정합성 카운터가 1 이상일 때 경고·알림을 띄우지 마라.** 재임베딩 중 정상 상태다
- 기존 테스트를 깨뜨리지 마라
