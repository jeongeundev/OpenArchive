# Step 8: access-panel-shares

문서 상세 「열람 범위」 패널에 「내 공유에 포함」 체크를 둔다.

## 읽어야 할 파일

- `/docs/UI_GUIDE.md` — 「열람 범위」 패널과 「내 공유에 포함」(step 0)
- `/docs/ADR.md` — **ADR-044** 「공유 (2026-10-02, #97 c)」 결정 2(공유 부여는 열람 범위와 별개 축 — 조직 공개·제한 모두 허용, 열람 범위 저장이 공유 부여를 지우지 않는다), 트레이드오프 ③
- `frontend/src/components/AccessPanel.tsx`, `AccessPanel.test.tsx` — 소유자 전용 렌더(부모가 소유자에게만), 조회 `AbortController`, 저장·오류 표시
- `frontend/src/app/documents/[id]/DocumentDetailView.tsx`
- `frontend/src/lib/api.ts` — step 6의 `listShares`·`addShareDocument`·`removeShareDocument`

## 작업

### 1) 테스트 먼저 — `frontend/src/components/AccessPanel.test.tsx`

1. 패널이 `listShares`로 내 공유를 불러 각 공유에 체크박스를 보이고, 이 문서가 든 공유는 체크되어 있다. **조직 공개·제한 둘 다에서** 보인다.
2. 체크 → `addShareDocument(shareId, documentId)`, 해제 → `removeShareDocument`. 열람 범위 「저장」 버튼과 독립적으로 즉시 반영되고, 성공·실패 문구를 보인다. 실패하면 체크 상태를 되돌린다.
3. 공유가 없으면 체크 목록 대신 설정 화면(`/settings`)에서 공유를 만들라는 안내 링크를 보인다.
4. 조직 공개 문서에서 공유에 포함되어 있으면 "조직 공개와 별개로 외부 공유에 열려 있다"는 뜻의 안내가 보인다.
5. 열람 범위 저장(`setDocumentAccess`) 뒤에도 공유 체크 상태가 바뀌지 않는다(다시 불러온 값 기준).
6. `listShares` 실패는 공유 영역에 오류로 보이고 열람 범위 편집은 그대로 쓸 수 있다.
7. 비소유자에게는 패널이 렌더되지 않으므로 `listShares`도 호출되지 않는다(기존 소유자 판정 테스트에 단언 추가).

### 2) 구현

- `AccessPanel` 안에 공유 영역을 두거나 `ShareToggles` 컴포넌트로 분리한다. 조회는 기존 조회와 같은 `AbortController`로 취소한다.
- 저장 중·편집 중(`disabled`)에는 체크를 막는다.

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build:static
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다(`check.sh`는 오래 걸린다 — 끝까지 기다린다. 실행하지 못했다면 사유를 summary에 적는다).
2. mutant 확인: 실패 시 되돌리기를 지우면 테스트 2가 실패하는가? 조직 공개일 때 숨기면 테스트 1이 실패하는가? 통과하면 보강한다.
3. `phases/m21-shares/index.json`의 step 8을 갱신한다. summary에 check.sh의 backend·frontend 테스트 수를 적는다.

## 금지사항

- 공유 포함을 열람 범위 저장 요청(`setDocumentAccess`)에 싣지 마라. 이유: 공유 부여는 별개 축이고 API도 따로다(ADR-044 「공유」 결정 2).
- 비소유자에게 공유 정보를 보이거나 조회하지 마라. 이유: 누가 이 문서를 외부에 열었는지가 샌다.
- 백엔드를 고치지 마라.
- 기존 테스트를 깨뜨리지 마라
