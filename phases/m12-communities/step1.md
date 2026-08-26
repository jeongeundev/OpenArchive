# Step 1: clusters-copy

## 배경 — 화면이 말하는 것을 계산과 맞춘다

step 0(`louvain-clusters`)이 `GET /api/clusters`의 묶는 축을 **태그 → 관계 그래프의 Louvain
군집**으로 바꿨다. API 응답 형태(`clusters[{name,size,documents}]`·`connections[{source,target,count}]`)는
그대로다. 그러나 `/clusters` 화면의 문구는 여전히 태그 묶음을 전제로 하고, 묶음이 **자동 판정된
추천**이라는 사실을 말하지 않는다.

Louvain 결과는 seed·입력에 따라 달라질 수 있는 "그럴듯한 분할 중 하나"다(step 0 배경의 실측).
ADR-029 정정이 `overlaps`에서 겪은 함정 — 화면 문구가 신호보다 강하게 단정해 틀리는 것 — 을
여기서 되풀이하지 않는다. `docs/UI_GUIDE.md` 「관계 종류 어휘」의 원칙("관계는 자동 판정된
추천이므로 사실처럼 단정하지 않는다")을 덩어리에도 적용한다.

**프론트 빌드 산출물은 백엔드에 동봉된다 (ADR-041).** 화면 소스를 고치면
`npm run build:static`으로 `backend/app/static/`을 갱신해야 실제 서빙 화면에 반영된다.
갱신분은 `git diff`에 드러나고 하네스가 함께 커밋한다. 빌드는 `generateBuildId` 고정으로
재현 가능하므로 소스가 같으면 산출물 diff가 나지 않는다.

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `docs/UI_GUIDE.md` — 「관계 종류 어휘」 절과 `/clusters` 항목(4번)
- `docs/ADR.md` **ADR-041**(빌드된 프론트를 백엔드 패키지에 동봉) · **ADR-027** 규칙 3
- `backend/app/services/clusters.py` — step 0이 바꾼 이름 규칙(최다 태그 → 없으면 연결 많은 문서 제목 → 겹치면 번호)과 "미분류"·"기타" 버킷
- `frontend/src/app/clusters/page.tsx` — 수정 대상
- `frontend/src/app/clusters/page.test.tsx` — 갱신 대상
- `frontend/src/lib/types.ts`(`Cluster`·`ClustersResponse`) — **변경하지 않는다**
- `frontend/package.json`(`build:static`) · `scripts/check.sh`

## 작업

### 1) 테스트를 먼저 고친다 — `frontend/src/app/clusters/page.test.tsx`

기존 케이스("덩어리를 SVG로 보여주고 클릭하면 문서 목록을 연다")는 유지하고, 아래를 더한다:

- 설명 문구에 **"관계"**가 들어가고 **"태그로 묶"**은 들어가지 않는다.
- 묶음이 추천임을 알리는 문장이 화면에 있다 (아래 문구의 마지막 문장).

### 2) `frontend/src/app/clusters/page.tsx` 문구

- 상단 `<p>현재 열람 범위 기준</p>`와 `<h1>주제 덩어리</h1>`, 내비게이션 라벨은 **그대로 둔다**
  (헤더·다른 화면·문서가 이 이름을 쓴다).
- 설명 문단을 아래 뜻으로 바꾼다 (표현은 다듬어도 되지만 세 문장의 사실은 지켜라):

  > 관계 그래프에서 서로 많이 이어진 문서끼리 묶었습니다. 원의 크기는 문서 수, 선의 굵기는
  > 덩어리 사이 관계 수입니다. 덩어리 이름은 가장 많이 쓰인 태그이고, 태그가 없으면 연결이
  > 가장 많은 문서의 제목입니다. 묶음은 관계로 계산한 추천이며 사실처럼 단정하지 않습니다.

- "미분류"는 **관계가 없는 문서**(대개 임베딩 대기 중)라는 뜻이 됐다. 그 덩어리를 선택했을 때
  목록 위 제목 옆에 짧은 보조 문구를 한 줄 둔다: *"아직 관계가 계산되지 않았거나 이어진 문서가
  없는 문서입니다."* — 이름이 `미분류`일 때만.
- 그 밖의 레이아웃·SVG·선택 동작은 바꾸지 않는다.

### 3) 정적 산출물 갱신

`cd frontend && npm run build:static` — `backend/app/static/`이 바뀐다. 이 변경을 **되돌리지
마라.** 그것이 실제 서빙되는 화면이다.

## Acceptance Criteria

```bash
cd frontend && npm run lint
cd frontend && npm run test
cd frontend && npm run build:static
grep -n "관계" frontend/src/app/clusters/page.tsx
test -z "$(grep -n '태그로 묶' frontend/src/app/clusters/page.tsx)"
test -n "$(git status --short backend/app/static)"      # 산출물이 갱신됐다 (문구가 바뀌었으므로 diff가 있어야 한다)
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가? (새 npm 의존성 없음)
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가? — "볼 수 없는 문서"에 관한 표시·자리표시를 새로 만들지 않았는가
3. 결과에 따라 `phases/m12-communities/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `frontend/src/lib/types.ts`·API 호출·백엔드 코드를 바꾸지 마라. 이유: API 계약은 step 0이 불변으로 고정했다. 이 step은 `frontend` 스코프 하나다.
- "주제"·"같은 내용"·"분류"처럼 묶음을 사실로 단정하는 문구를 새로 넣지 마라. 이유: Louvain 결과는 분할 중 하나이며 UI_GUIDE의 어휘 원칙에 어긋난다.
- 볼 수 없는 문서를 암시하는 표시(🔒, "숨겨진 N건" 등)를 넣지 마라. 이유: ADR-027 규칙 2 — 표시 자체가 존재와 개수를 누출한다.
- `backend/app/static/` 변경을 되돌리거나 손으로 고치지 마라. 이유: 그것은 빌드 산출물이며 소스에서만 갱신한다(ADR-041).
- `npm run build`(일반 빌드)로 검증을 끝내지 마라. 이유: 동봉 산출물이 갱신되지 않아 소스와 조용히 어긋난다. `build:static`을 써라.
- 실패하는 테스트를 skip·삭제하거나 단언을 약화하지 마라.
- `git commit`을 직접 하지 마라. 이유: 커밋은 execute.py가 한다.
- 기존 테스트를 깨뜨리지 마라.
