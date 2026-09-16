# Step 7: docs-relations

## 배경 — 코드가 바뀐 만큼 정본 문서를 맞춘다

step 0~6이 관계 판정·저장·조회·라벨·CLI·시연 코퍼스를 바꿨다. ADR-029(관계를 저장 시점에
만든다)와 ADR-042(Louvain 군집)의 결정 일부가 **거짓**이 됐고, `ARCHITECTURE.md`의 SQL 발췌와
"양방향 두 행" 설명, `CLAUDE.md`의 규칙, 운영 명령 목록이 낡았다. 이 step은 코드를 바꾸지 않고
문서만 맞춘다. **아래 수치는 #94 스파이크와 기준선 측정의 실측값이다 — 고쳐 쓰지 말고 그대로 옮겨라.**

### 바뀐 것 (step 0~6 산출물 — 각 파일을 읽고 확인하라)

| step | 산출물 | 문서에 반영할 사실 |
|---|---|---|
| 0 | `backend/migrations/014_edges_triggers.sql` | 판정 본체는 `rebuild_document_edges(uuid)`(함수 정의 `SET hnsw.ef_search=200`·`random_page_cost=1.1`·`enable_seqscan=off`), 트리거 함수는 호출만. `overlaps` = 양쪽 비율 ≥ 0.8 AND `matched ≥ 2`. 문서당 이웃 상한 5(겹친 대목 ↓·최소 거리 ↑). **단방향 저장** — `src` = 계산 주체, `DELETE WHERE src = NEW`만 |
| 1 | `backend/app/worker.py` | `finished_at = clock_timestamp()` — 잡 시간에 트리거 시간이 포함된다 |
| 2 | `backend/app/services/search.py` | `traversal_edges`가 역방향(`dst→src`, chunk_index 뒤집음)을 UNION ALL |
| 3 | `backend/app/services/related.py` | `neighbors` CTE가 `src=X ∪ dst=X`, 같은 이웃은 `DISTINCT ON`으로 overlaps 우선 |
| 4 | `backend/app/services/clusters.py` | 라벨 = (군집 안 빈도 − 밖 빈도) 최대 태그, 양수가 없으면 중심 문서 제목 |
| 5 | `backend/app/services/system.py`·`cli.py` | `rebuild_all_edges(conn)` · `openarchive rebuild-edges [--dsn]` — 문서마다 커밋 |
| 6 | `scripts/demo_corpus/`·`seed_demo.py` | 12~16건이 2~4청크, `seed_demo.py`가 끝에 전량 재계산 |

### 실측 — 그대로 옮길 수치

**기준선(변경 전, 실 BGE-M3, `scripts/eval_search.py`, 2026-09-16).** A = 로컬 pgvector 컨테이너,
C = 실 OpenSQL VM. 평가셋은 `notes/corpus93/eval/{a,c}.json`(git 밖, 라이선스 때문).

| 코퍼스 | 질의 | R@1 | R@5 | R@10 | MRR | 1위 | 미스 |
|---|---|---|---|---|---|---|---|
| A (184문서·1,080청크) | 30 | 0.578 | 0.883 | 0.900 | 0.802 | 22 | 2 |
| C (104문서·2,211청크) | 32 | 0.552 | 0.969 | 0.969 | 0.938 | 28 | 0 |

C의 R@1이 낮은 것은 정답이 판본 포함 2~3개라 상한이 1/n인 탓이다. **변경 후 수치는 phase 뒤
사용자가 재고 이슈 #94에 적는다** — 이 step에서는 "변경 전"으로만 적는다.

**규칙 후보 시뮬레이션**(덤프한 실 임베딩 위에서 청크 kNN을 파이썬으로 계산하고 규칙을 바꿔 가며
`_assign_communities`에 넘김. 적재 순서대로 "처리 시점까지의 문서만 후보"로 재현했더니 실제 저장
edge와 **A 자카드 1.000(kind까지 일치)·C 0.989**). 순도 = 덩어리별 최다 분류 비율의 크기 가중 평균
(C는 기능 분류 9종, A는 디렉토리 23종). 도구: `notes/corpus93/scripts/simulate_rules.py`(git 밖).

| 규칙 (순차 적재) | 코퍼스 | 쌍 | overlaps | 정밀도 | 재현율 | related 동일분류 | 밀도 | deg max / mean | 순도 | 재실행 자카드 |
|---|---|---|---|---|---|---|---|---|---|---|
| 008 (현재) | C | 2,163 | 143 | 0.168 | 1.000 | 0.198 | 0.404 | 92 / 41.6 | 0.385 → 재실행 후 0.375 | 0.971 |
| 단방향 + 양쪽 비율 | C | 2,163 | 59 | 0.407 | 1.000 | 0.206 | 0.404 | 92 / 41.6 | 0.385 | 0.990 |
| + 비율 0.9 | C | 2,163 | 34 | 0.647 | **0.917** | 0.209 | 0.404 | 92 / 41.6 | 0.385 | 0.990 |
| + cap 10 | C | 971 | 59 | 0.407 | 1.000 | 0.304 | 0.181 | 75 / 18.7 | 0.558 | 0.991 |
| **+ cap 5 (채택)** | C | 505 | 59 | 0.407 | 1.000 | 0.390 | 0.094 | 48 / 9.7 | **0.577** (전체 기준 0.587) | 0.986 |
| + cap 3 | C | 306 | 57 | 0.421 | 1.000 | 0.434 | 0.057 | 35 / 5.9 | 0.558 | 0.984 |
| N=5 + cap 5 | C | 497 | 37 | 0.622 | 0.958 | 0.380 | 0.093 | 53 / 9.6 | 0.567 | 0.988 |
| 008 (현재) | A | 2,221 | 357 | — | — | 0.234 | 0.132 | 98 / 24.1 | 0.505 | 0.978 |
| **+ cap 5 (채택)** | A | 899 | 138 | — | — | 0.390 | 0.053 | 31 / 9.8 | **0.543** (전체 기준 0.554) | 0.993 |
| + cap 3 | A | 546 | 125 | — | — | 0.406 | 0.032 | 19 / 5.9 | 0.505 | 0.991 |

- overlaps 정밀도의 정답은 C의 **판본 24쌍**(동명 21 + 개명 3). 남는 35쌍은 위원회 운영규칙 등
  규정 상용구 유사 — **임베딩 모델의 한계로 기록**한다.
- "순차 적재 vs 전체 코퍼스 기준" 문서쌍 일치(j_full)는 cap5에서 **C 0.371 · A 0.477**, 적재 순서를
  섞으면 **0.310 · 0.369**만 겹친다. 순도는 둔감(0.577 vs 0.587)하지만 쌍은 흔들린다 →
  `rebuild-edges`의 근거.
- 시연 코퍼스(64문서·1청크·실 BGE-M3): 008 순차 0.844 → 재실행 3건 뒤 **0.688**(§15 (c) 재현),
  전체 기준 0.734. cap5 순차 0.609, 전체 기준 **0.750**.
- 기각: `mutual`(양쪽 다 계산됐을 때만 related) — 순차 적재에서 먼저 들어온 문서는 나중 문서를 계산할
  기회가 없어 related가 0건이 된다. 트리거 안 역방향 재계산 — 비용 5배·kNN 비대칭이라 불완전.
- P1(#93): VM 실측 프로브 265ms vs `enable_seqscan=off` 14ms, 트리거 전체 10청크 4.6s→0.2s·159청크 40s→2.7s.
  `SET LOCAL`은 OpenProxy 풀 백엔드의 PL/pgSQL generic plan 때문에 안 먹고 `DISCARD PLANS` 뒤에야 먹었다 → 함수 정의 `SET`.

## 읽어야 할 파일

- 위 표의 step 0~6 산출물 **전부** (코드가 정본이다 — 문서를 코드에 맞춘다)
- `CLAUDE.md` — 「아키텍처 규칙」 절
- `docs/ADR.md` — **ADR-029**(전문과 정정 블록) · **ADR-042**(결정 4) · **ADR-039**(CLI 명령 목록이 있으면) · 개정 표기 관례(ADR-029 "⚠️ 정정" 블록, ADR-042 "이름 변경" 블록)
- `docs/ARCHITECTURE.md` — 「DB 스키마」의 `document_edges` 주석 · 「자동 임베딩 파이프라인」 트리거 절 · 「검색 데이터 흐름」 · 「관련 문서·태그 추천」(SQL 발췌와 *"저장된 score는 양방향이 같지만, 그것은 근사다"* 문단)
- `docs/OPENSQL_RESEARCH.md` — §14·§15의 형식(§16을 같은 형식으로 신설)
- `docs/UI_GUIDE.md` — 「관계 종류 어휘」
- `docs/OPERATIONS.md` · `README.md` — CLI 명령 목록
- `docs/ROADMAP.md` — 관계·평가 관련 항목이 있으면 확인

## 작업

### 1) `docs/ADR.md`

**ADR-029에 개정 블록**(`> **개정 (2026-09-16, #94).**` 형식)을 결정 3·4 아래에 넣고 본문 표현을 맞춘다:

- 결정 3의 *"양방향은 한 번 계산해 두 행으로 저장한다"* → **폐기.** 단방향 저장(`src` = 계산 주체,
  `DELETE WHERE src = NEW`만) + 조회 대칭(검색 순회·관련 문서·태그 추천·군집·진단이 `src ∪ dst`로
  읽는다). 근거: 재실행 자카드 0.971→0.990, 재실행만으로 순도 0.385→0.375(C)·0.844→0.688(시연)가
  떨어지던 것이 멈춘다.
- 결정 4 개정: 비율은 **양쪽** 모두 0.8 이상, 문서당 이웃 **5건** 상한(정렬 키 명시). 표의 수치.
  `NEIGHBOR_N=10` 유지 근거(n=5와 ±0.01). 비율 0.9 기각 근거(판본 2쌍 놓침). 정정 블록(2026-08-11)의
  "청크 수 하한을 어디에 걸어도 걸러지지 않는다"는 **양쪽 비율로 해소**됐음을 적되, 정밀도 0.41이
  1.0이 아닌 이유(상용구 유사)를 한계로 남긴다. 화면 어휘 *"여러 대목에서 만난다"*는 유지.
- **결정 6 신설 — 전량 재계산.** 트리거는 처리 시점까지의 문서만 후보로 본다(순서 의존의 본체,
  j_full 0.37~0.48). `rebuild_document_edges(uuid)`·`rebuild_all_edges`·`openarchive rebuild-edges`.
  기각한 대안(역방향 재계산·mutual)과 근거. 트레이드오프: 대량 적재 뒤 사용자가 한 번 실행해야
  한다 — 이것은 "최신 수렴"의 범위 안이며 "실시간"을 약속하지 않는다(ADR-015 어휘).
- P1: 판정 본체 함수의 `SET enable_seqscan = off`. `SET LOCAL`이 안 되는 이유. 트리거 비용 수치.
- **m7~m9 구현 대조 결과** 문단의 *"임베딩 완료 지연이나 롤백 증가가 관측되지 않아 `edge_jobs` 재검토
  조건은 발동하지 않았다"* 뒤에 #93의 40초 실측과 P1로 2.7초가 된 사실을 덧붙인다.

**ADR-042 결정 4에 개정 블록**: 이름 = (군집 안 빈도 − 밖 빈도)가 양수인 태그 중 최대, 없으면
중심 문서 제목. 근거는 #93 G2(「2025판」「2025판 (2)」「2025판 (3)」)와 §15 (b)(「경영지원」 둘).
"같은 태그 이름의 덩어리 둘은 원리상 생기지 않는다"를 적는다.

**ADR-039**(또는 CLI 명령을 나열한 ADR)에 `rebuild-edges` 한 줄.

### 2) `docs/ARCHITECTURE.md`

- `document_edges` 스키마 주석: 단방향 저장·조회 대칭 한 문장.
- 트리거 절: `trg_build_document_edges` → `build_document_edges()` → `rebuild_document_edges(uuid)` 호출 구조, 함수 SET 세 값.
- 「검색 데이터 흐름」: `traversal_edges`의 역방향 UNION 발췌 갱신.
- 「관련 문서·태그 추천」: SQL 발췌를 step 3의 `neighbors`/`best` CTE로 바꾸고, *"저장된 score는 양방향이
  같지만, 그것은 근사다"* 문단을 **삭제**하고 "한 방향만 저장하고 조회에서 합친다 — 같은 이웃이 양방향에서
  kind가 다르면 overlaps를 남긴다"로 교체.
- 「관계 지도」(`GET /api/clusters`) 행: 라벨 규칙 한 줄.
- 운영 절이 있으면 `rebuild-edges`를, 없으면 `docs/OPERATIONS.md`에.

### 3) `docs/OPENSQL_RESEARCH.md` — **§16 신설** «관계 판정 규칙 재설계 실측 [실측 2026-09-16]»

§15와 같은 형식(측정 환경과 재현 방법 → 표 → 결론 → 하지 않은 것과 그 이유). 위 「실측」 절의
표와 불릿을 **전부** 옮긴다. 재현 명령은 `notes/corpus93/`(git 밖)임을 밝히고
`scripts/eval_search.py`의 실행 예를 적는다. §15 말미의 *"규모가 다른 코퍼스에서 같은 스윕을 돌리기
전에는 'N=10이 과하다'고 말할 수 없다"*에 §16으로 답이 났음(N=10 유지, 손잡이는 문서당 상한)을
§15에 한 줄 덧붙인다.

### 4) `docs/UI_GUIDE.md` 「관계 종류 어휘」

`overlaps` 설명의 *"`score`는 자기 대목 중 상대 문서에서 최근접 이웃을 찾은 비율"*을 **양쪽** 기준으로
고친다(저장 `score`는 자기 쪽 비율이지만 판정은 양쪽). 어휘 *"여러 대목에서 만난다"*·진단 점수
*"닿은 대목 N%"*는 그대로. 관계 지도 덩어리 이름이 "군집에 집중된 태그"임을 한 줄.

### 5) `docs/OPERATIONS.md` · `README.md`

CLI 명령 목록에 `openarchive rebuild-edges` — *대량 적재 뒤 한 번. 트리거는 처리 시점까지의
문서만 이웃 후보로 보므로.* README는 명령 목록이 있는 절에만 한 줄.

### 6) `CLAUDE.md` 「아키텍처 규칙」에 두 줄

- *`document_edges`는 **단방향 저장**이다 — `src_document_id`가 계산한 문서이고 재계산은 자기 `src` 행만
  교체한다. 읽는 쪽(검색 순회·관련 문서·태그 추천·군집·진단)은 반드시 `src ∪ dst`로 읽는다. 이유:
  양방향 두 행 + DELETE both는 남이 발견한 관계를 지웠다(#93 R4, ADR-029 개정).*
- *관계 판정 함수 `rebuild_document_edges`는 **함수 정의 `SET enable_seqscan = off`**를 유지한다. 1만 청크
  미만에서 플래너가 HNSW를 안 골라 트리거가 청크당 0.26초였다. `SET LOCAL`은 OpenProxy 풀 백엔드에
  남는 generic plan 때문에 안 먹는다. 대량 적재 뒤에는 `openarchive rebuild-edges`로 전체 기준으로
  수렴시킨다 (ADR-029 결정 6).*

기존 규칙 중 *"권한·태그 필터를 벡터 정렬 서브쿼리 안에 두는 것은 문제가 없다"* 등은 손대지 않는다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_architecture.py tests/test_frontend.py -q
cd backend && .venv/bin/python -m pytest -q
grep -c "rebuild_document_edges" docs/ADR.md docs/ARCHITECTURE.md CLAUDE.md
grep -c "rebuild-edges" docs/OPERATIONS.md docs/ADR.md
grep -n "^## 16\." docs/OPENSQL_RESEARCH.md
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. `grep -c` 결과는 각 파일에서 **1 이상**, `^## 16.`은 한 줄이어야 한다.
2. 문서 대조 체크리스트:
   - ADR-029 개정 블록의 수치가 위 「실측」 표와 **글자 단위로** 같은가?
   - `ARCHITECTURE.md`의 SQL 발췌가 `search.py`·`related.py`의 실제 SQL과 어긋나지 않는가?
   - "항상 최신"·"실시간 동기화"를 쓰지 않았는가? (CLAUDE.md 어휘 규칙)
   - 코드 파일을 한 줄도 바꾸지 않았는가?
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 7을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실측 수치를 추정·반올림·보정하지 마라. 이유: 이 문서들이 ADR·이슈 코멘트·결과보고서의 정본이 된다.
- "변경 후" 평가셋 수치를 적지 마라. 이유: 아직 재지 않았다. phase 뒤 사용자가 잰다.
- 코드·마이그레이션·테스트를 고치지 마라. 이유: 이 step은 문서 스코프다. 코드와 문서가 어긋나면 **문서를 코드에 맞춘다**.
- ADR-029·042의 기존 본문을 지우고 다시 쓰지 마라. 이유: 이 저장소의 ADR은 결정 이력을 개정 블록으로 쌓는다.
- "관계 지도"를 "주제 덩어리"로 되돌리거나 덩어리를 "주제·분류"로 단정하는 어휘를 쓰지 마라 (ADR-042 결정 5).
