# Step 6: edges-trigger

## 배경 — 이 파일이 "DB 안에서 관계가 만들어진다"의 본체다

`003_triggers.sql`이 *"임베딩 갱신이 DB 계층에서 자동 트리거된다"*의 본체였듯, 이 파일이
*"청크 사이의 지형도 DB 안에서 만들어진다"*의 본체다. **워커 코드는 한 줄도 바뀌지 않는다.**

발화 지점은 `documents.embedding_status`가 `ready`로 바뀔 때다. 워커의 `finalize_job`이
`DELETE` → `INSERT` 청크 → `status='ready'` 순서로 도므로(`worker.py:145`), 이 시점이면
**청크가 전부 들어가 있고 같은 트랜잭션 안**이다.

### 양방향을 어떻게 다루는가 — 이 step의 핵심 설계

새 문서 C가 들어오면 C 기준으로만 이웃을 찾을 수 있다. 기존 문서 A는 재임베딩되지 않으므로
**A가 C를 발견할 기회가 영원히 없다.** 그래서 **한 번 계산해 양방향 행을 함께 넣는다.**

```
C의 청크 → 이웃 조회 1회
  ↓
(C → A)  그리고  (A → C)   둘 다 INSERT   ← score는 같은 값
```

- **대칭으로 간주하는 것은 근사다.** 정확히는 A 기준 kNN과 C 기준 kNN이 다르다(kNN은
  비대칭이다). 정확한 역방향은 전체 문서를 재계산해야 하므로 **비용이 문서 수에 비례해
  터진다.** 근사를 택하고, 그 사실을 트리거 주석과 ADR-029에 적는다
- **`broader`만 예외다** — 방향이 의미이므로 판정된 방향 하나만 넣는다
- 이 설계가 **삭제 범위 문제도 함께 푼다**. 트리거는
  `WHERE src = NEW.id OR dst = NEW.id`로 지우는데, 대칭 저장이라 B를 재임베딩하면
  `(A↔B)` 두 행이 모두 B 기준으로 다시 만들어진다

## 읽어야 할 파일

- `backend/migrations/003_triggers.sql` — **트리거 파일의 서술 밀도와 조건 설명 방식.
  같은 수준으로 쓴다.** `AFTER`·`UPDATE OF`·`pg_trigger_depth()` 셋을 왜 골랐는지 적어 둔
  주석이 이 파일의 본보기다
- `backend/app/worker.py` `finalize_job` — 발화 시점의 트랜잭션 상태.
  **문서 행이 `FOR UPDATE`로 잠겨 있다**
- `docs/OPENSQL_RESEARCH.md` **§14** — `NEIGHBOR_N` · `OVERLAP_RATIO` · `BROADER_MARGIN`의
  확정값. **이 값을 그대로 쓴다**
- `docs/ADR.md` **ADR-029** · **ADR-011 보강 4·5** — 설계 근거와 HNSW 설정 규칙

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_triggers.py`에 추가

**tdd-guard가 `*_triggers.sql`에 대해 이 파일을 요구한다.** 실제 컨테이너에서 돈다.

단언할 것:

- **문서를 넣고 임베딩이 끝나면 edge가 생기는가** — 워커를 돌리거나 `finalize_job`을
  직접 호출해 `status='ready'`를 만든다
- **양방향이 함께 생기는가** — `(A→B)`가 있으면 `(B→A)`도 있다 (`broader` 제외)
- **자기 문서와의 edge가 없는가**
- **재임베딩하면 그 문서의 edge가 갈아끼워지는가** — 개수가 두 배가 되지 않는다
- **`kind`가 실제로 갈리는가** — 거의 같은 내용의 문서 쌍은 `overlaps`,
  한 대목만 겹치는 쌍은 `points_to`가 나온다. 판정 로직이 도는지 보는 유일한 방법이다
- **문서를 지우면 edge가 CASCADE로 사라지는가**
- **제목·태그만 바꾼 UPDATE로는 트리거가 발화하지 않는가** — 발화하면 관계가 매번
  재계산되어 비용이 샌다

> **`kind`가 갈리는지 보는 테스트를 빼지 마라.** edge가 생기는지만 보면 판정이 전부
> 한 종류로 뭉쳐도 통과한다 — step 2에서 잰 상수가 통째로 무의미해진다.

### 2) `backend/migrations/008_edges_triggers.sql`

**함수에 `SET` 절을 붙인다** — 함수 안에서 `SET LOCAL`을 쓰면 그 트랜잭션 나머지에도
남지만, 함수 정의의 `SET`은 **함수 범위로 한정되어 종료 시 복원된다.**

```sql
CREATE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
  SET hnsw.ef_search = 200
  SET random_page_cost = 1.1
AS $$ ... $$;
```

> ⚠️ **두 설정이 없으면 플래너가 HNSW를 아예 고르지 않는다**(ADR-011 보강 5).
> `hnsw.ef_search`는 pgvector가 정의한 커스텀 GUC라 `SET` 절에 쓸 수 있는지 확인이 필요하다 —
> **함수 생성이 실패하면** `SET LOCAL` 두 줄을 함수 본문 맨 앞에 두고, **왜 그렇게 했는지와
> 트랜잭션 나머지에 남는다는 사실**을 주석에 적는다.

본문 구조:

```
1) 이 문서가 걸린 edge를 전부 지운다   (src = NEW.id OR dst = NEW.id)
2) 청크당 이웃 NEIGHBOR_N개를 한 번 조회한다   (CROSS JOIN LATERAL + ORDER BY <=> + LIMIT)
3) 문서 쌍으로 집계한다   (매칭 청크 수 ÷ 내 청크 수 = 비율,  최소 거리와 그 청크 위치)
4) 비율로 kind를 가른다   비율 >= OVERLAP_RATIO → overlaps
                          그 미만              → points_to (최소 거리 청크쌍을 기록)
5) word_similarity 양방향 차로 broader를 판정한다   차 >= BROADER_MARGIN → broader
                                                    미달                 → related
6) 양방향으로 INSERT한다   (broader 제외)
```

**중간 결과는 전부 CTE다.** 임시 테이블을 쓰지 마라 — OpenProxy가 백엔드를 넘길 때
`DISCARD ALL`을 하지 않아 다음 클라이언트로 누수된다(ADR-022, 실측).

**트리거 조건 셋을 정확히 고른다.**

| 조건 | 이유 |
|---|---|
| `AFTER` | 청크가 확정된 뒤여야 한다 |
| `UPDATE OF embedding_status` | 제목·태그 변경으로 재계산이 돌면 비용이 샌다 |
| `WHEN (NEW.embedding_status = 'ready' AND OLD.embedding_status IS DISTINCT FROM 'ready')` | `ready → ready` 재진입 차단 |

`003_triggers.sql`이 세 조건을 각각 왜 골랐는지 적어 둔 것처럼, **여기도 조건마다 이유를 적는다.**

### 3) 주석에 반드시 남길 것

- **양방향 근사**와 그 대가(정확한 역방향은 전체 재계산이라 문서 수에 비례해 터진다)
- **`score`의 척도가 `kind`마다 다르다** — 섞어 정렬하면 안 된다
- **이 트리거가 `finalize_job` 트랜잭션 안에서 돈다**는 것과 step 2가 잰 실제 비용
- **판정이 실패하면 임베딩까지 롤백된다** — 대가를 숨기지 않는다
- `NEIGHBOR_N` 등 상수 옆에 **§14 인용**. 값만 있으면 다음 사람이 못 바꾼다

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
python -m pytest tests/test_triggers.py -q

# 2) kind가 실제로 갈리는지 보는 테스트가 있는가 — 이름만으로는 통과할 수 없다
grep -cE "overlaps" tests/test_triggers.py      # 1 이상
grep -cE "points_to" tests/test_triggers.py     # 1 이상

# 3) 마이그레이션이 적용되는가
ls migrations/008_edges_triggers.sql

# 4) seed 데이터 위에서 실제로 edge가 만들어지는가
python3 ../scripts/seed_demo.py --reset && python3 ../scripts/seed_demo.py
psql "$DATABASE_URL" -c "SELECT kind, count(*) FROM document_edges GROUP BY 1 ORDER BY 2 DESC"
#   → 최소 두 종류 이상이 0이 아니어야 한다. 한 종류만 나오면 판정이 안 갈린 것이다

# 5) 양방향이 들어갔는가
psql "$DATABASE_URL" -c "
  SELECT count(*) FROM document_edges a
   WHERE a.kind <> 'broader'
     AND NOT EXISTS (SELECT 1 FROM document_edges b
                      WHERE b.src_document_id = a.dst_document_id
                        AND b.dst_document_id = a.src_document_id
                        AND b.kind = a.kind)"
#   → 0이어야 한다

# 6) 자기 자신과의 edge가 없는가
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_edges WHERE src_document_id = dst_document_id"
#   → 0

# 7) 트리거가 HNSW를 타는가 — 함수 정의에 설정이 들어갔는지
psql "$DATABASE_URL" -c "SELECT proconfig FROM pg_proc WHERE proname='build_document_edges'"
#   → ef_search·random_page_cost가 보이거나, 본문에 SET LOCAL이 있어야 한다

# 8) 임시 테이블을 쓰지 않았는가 — 출력이 없어야 한다
grep -niE "create +(temp|temporary) +table" migrations/008_edges_triggers.sql

# 9) 워커 코드가 안 바뀌었는가 — 출력이 없어야 한다
git diff --name-only | grep "app/worker.py"

# 10) 기존 테스트 전부 통과
python -m pytest -q

# 11) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **step 1의 seed와 워커가 필요하다.**
2. 아키텍처 체크리스트를 확인한다:
   - **테스트가 구현보다 먼저 쓰였는가?**
   - **AC 4번에서 `kind`가 두 종류 이상 나왔는가?** 한 종류뿐이면 step 2의 상수가
     틀렸거나 판정 SQL이 잘못된 것이다. **"edge가 생겼으니 됐다"로 넘기지 마라**
   - **트리거가 `random_page_cost`와 `ef_search`를 실제로 걸었는가?** 하나만 빠져도
     Seq Scan으로 떨어져 문서 하나 처리가 수백 배 느려진다
   - **문서 하나를 넣었을 때 실제 소요가 step 2의 예측과 맞는가?** 크게 벗어나면
     그 사실을 `summary`에 적어라 — ADR-029가 `edge_jobs` 재검토 조건을 적어 뒀다
   - **재진입이 막혔는가?** `ready → ready`로 다시 발화하면 같은 계산이 반복된다
   - **임시 테이블을 쓰지 않았는가?** (ADR-022)
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`backend/app/worker.py`를 수정하지 마라.** 이유: 이 설계의 이점이 *"워커 코드 변경 0줄"*
  이다. 워커가 edge를 만들면 ADR-029 결정 3이 무너진다
- **임시 테이블을 쓰지 마라.** 이유: OpenProxy 누수 (ADR-022). 중간 결과는 CTE다
- **절대 거리 임계를 넣지 마라.** 이유: 컷오프는 `LIMIT NEIGHBOR_N`이다 (ADR-029 결정 4)
- **`SET LOCAL`을 함수 본문에 넣고 그 사실을 안 적지 마라.** 이유: 트랜잭션 나머지에
  남는다. 함수 `SET` 절이 안 되는 경우에만 쓰고, 반드시 주석에 남긴다
- **`kind`에 저장하지 않기로 한 종류를 넣지 마라.** 이유: ADR-029 결정 2
- **edge 생성 실패를 조용히 삼키지 마라.** 이유: 트리거 안에서 예외를 먹으면
  *"관계가 없는 문서"*와 *"판정이 터진 문서"*가 구분 불가능해진다. 터지게 두고
  워커의 재시도·백오프가 처리하게 한다
- **`avg(embedding)`을 쓰지 마라.** 이유: #29가 지적한 손실이 정확히 그것이고,
  이 트리거는 그것을 푸는 자리다
