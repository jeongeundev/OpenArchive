# Step 3: rrf-search

## 배경 — #12를 흡수하되 방식이 바뀐다

#12(M6 RRF)는 원래 `to_tsvector('simple', ...)`를 `GENERATED ALWAYS AS ... STORED` 컬럼으로
두는 안이었다. **한국어에서 사실상 작동하지 않는다** — #29가 재측정했다.

| 질의 | `tsvector(simple)` | `pg_trgm` |
|---|---|---|
| `OpenSQL` | **0건** (`opensql의`로 토큰화) | **2건** |
| `정합성` | **0건** (`정합성은`) | **1건** |
| `임베딩` | 1건 | **2건** |

조사가 붙어 토큰이 갈라지기 때문이고, 번들에 한국어 형태소 분석기가 없다.
**`pg_trgm`은 조사와 무관하게 찾는다.**

> ⚠️ **이 표는 4문장짜리 표본으로 잰 것이다**(#29가 스스로 밝혔다). 방향은 명확하지만
> **실제 문서 규모에서의 랭킹 품질은 아직 안 쟀다.** 이 step이 그것을 잰다.

**착수 시 #12 티켓 본문을 정정한다** — `tsvector` 원안이 남아 있으면 다음 사람이
그것을 근거로 쓴다.

### 왜 이것이 "같은 계층"의 증거인가

외부 벡터 DB 구성이라면 키워드 검색은 RDBMS가, 벡터 검색은 벡터 DB가 하고 **두 결과를
애플리케이션이 합쳐야 한다.** 여기서는 **한 SQL에서 융합**된다.

## 읽어야 할 파일

- `backend/app/services/search.py` — **`SEARCH_SQL` 전부.** m7 step 8이 그래프 확장을
  얹어 뒀으므로 지금 형태를 정확히 파악하라
- `docs/ADR.md` **ADR-011 보강 2** — **`DISTINCT ON`을 RRF 융합 *이후*로 옮겨야 한다**
- `docs/ADR.md` **ADR-016** — #12의 원래 결정. step 5가 정정한다
- `backend/migrations/005_trgm_extensions.sql` — m7이 이미 만든 `pg_trgm`
- `docs/OPENSQL_RESEARCH.md` **§14** — 측정 기록의 자리

## 작업

### 1) 테스트를 먼저 쓴다

- **정확한 단어로 찾을 때 벡터만보다 나은가** — 조사가 붙은 단어를 질의로 준다
- **벡터가 잡던 것을 잃지 않는가** — 의미는 비슷한데 단어가 다른 문서가 여전히 나온다
- 두 랭킹이 **같은 문서를 다르게 매겼을 때** 융합 결과가 합리적인가
- **권한이 두 경로 모두에 걸리는가** — `tests/test_visibility.py`에 융합 검색 단언 추가.
  키워드 경로에 권한을 안 걸면 **private 본문이 매칭돼 새어 나온다**
- **`DISTINCT ON`이 융합 이후인가** — 문서당 1건으로 줄이는 시점이 잘못되면
  점수가 아니라 UUID 순으로 잘린다

### 2) 인덱스

`pg_trgm`의 GIN 인덱스(`gin_trgm_ops`)를 `document_chunks.content`에 건다.

- **새 마이그레이션 파일**로 만든다 (`012_trgm_indexes.sql`)
- **인덱스가 실제로 쓰이는지 `test_indexes.py`가 확인한다** — 행이 적으면 Seq Scan을
  고르므로, 충분한 행을 넣거나 계획 형태만 본다
- 인덱스 크기를 측정해 §14에 적어라 — HNSW가 47MB였던 전례가 있다

### 3) 융합 쿼리

```
벡터 랭킹     ORDER BY embedding <=> qvec       LIMIT k * 배수
키워드 랭킹   ORDER BY similarity(content, q)   LIMIT k * 배수
    ↓
RRF 융합      Σ 1 / (K + rank)
    ↓
DISTINCT ON   문서당 1건        ← 반드시 융합 이후 (ADR-011 보강 2)
    ↓
정렬 + LIMIT k
    ↓
그래프 확장   (m7 step 8)
```

- **단일 SQL이다.** 두 결과를 앱에서 합치면 이 기능의 논지가 사라진다 (`CLAUDE.md` CRITICAL)
- **RRF 상수 `K`**(관례상 60)를 모듈 상수로 두고 근거를 적는다
- **후보 `LIMIT`이 `ef_search` 벽에 걸리지 않게 한다** — `MAX_K * 배수 < EF_SEARCH`.
  벡터 경로에만 해당하지만, 키워드 경로 `LIMIT`도 같이 커지면 융합 비용이 는다
- **권한 술어를 두 서브쿼리 *안*에** 넣는다. `VISIBLE_TO_USER`를 쓴다
- `SET LOCAL` 두 줄은 그대로 (`apply_vector_search_settings`)

### 4) 실제 규모에서 잰다 — §14에 덧붙인다

#29가 못 잰 것이 여기서 처음 측정된다.

- seed 데이터(문서 50+·청크 200+)에서 **질의 10개 정도**로 두 랭킹을 비교한다
- **융합이 실제로 나아지는가** — 벡터만·키워드만·융합 셋을 나란히 놓는다
- 소요와 계획(GIN 인덱스를 타는가)
- **`K` 값을 바꿔 본다** — 60이 이 데이터에서도 합리적인지

> 🔴 **융합이 나아지지 않으면 그 사실을 적어라.** #12는 원래 "조건부 채택"이었고
> ADR-016도 *"기대 효과가 실측 전까지 불확실하다"*고 적혀 있다. **나빠졌다면 채택하지
> 않는 것도 결과**다 — 그 경우 step 4를 건너뛰고 step 5가 ADR-016을 "미채택"으로 정리한다.

## Acceptance Criteria

```bash
cd backend

# 1) 테스트 통과
python -m pytest tests/test_search.py tests/test_visibility.py tests/test_indexes.py -q

# 2) 권한이 키워드 경로에도 걸렸는가 — 두 번 나와야 한다
grep -c "VISIBLE_TO_USER" app/services/search.py    # 2 이상

# 3) DISTINCT ON이 융합 이후인가 — 눈으로 확인할 것
grep -n -A5 -B5 "DISTINCT ON" app/services/search.py

# 4) 단일 SQL인가 — 파이썬에서 합치지 않았는지
git diff app/services/search.py | grep -nE "^\+.*(sorted\(|merge|for .* in .*results)"
#   → 융합 로직이 파이썬에 있으면 안 된다

# 5) tsvector를 쓰지 않았는가 — 출력이 없어야 한다 (여기는 backend/ 안이다)
#    `2>/dev/null`을 붙이지 마라. 경로가 틀려도 에러가 삼켜져 "출력 없음"이 거짓으로 성립한다
grep -rn "to_tsvector" app/ migrations/

# 6) 측정이 §14에 덧붙었는가
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' ../docs/OPENSQL_RESEARCH.md | grep -niE "RRF|trgm"

# 7) 실제 비교 — 세 방식이 나란히 있는가
sed -nE '/^## 14\./,/^(## 15\.|---)$/p' ../docs/OPENSQL_RESEARCH.md | grep -nE "벡터만|키워드만|융합"

# 8) #12 티켓이 정정됐는가
gh issue view 12 --json body -q .body | grep -c "pg_trgm"     # 1 이상
gh issue view 12 --json body -q .body | grep -c "to_tsvector" # 0 이어야 한다

# 9) 실제 응답
#    ⚠️ 검색은 **POST /api/search**다. GET으로 치면 405가 나오고 `| head`에 먹혀
#    "출력이 있다"로 거짓 통과한다 — m7 step 8 AC 9번이 정확히 이렇게 아무것도
#    검증하지 못했다. 그리고 m8이 익명 읽기를 닫았으므로(ADR-028) 로그인이 먼저다
export TEST_ADMIN_PW="${TEST_ADMIN_PW:-harness-local-check}"
ADMIN_PASSWORD="$TEST_ADMIN_PW" python ../scripts/create_admin.py admin --admin \
  || echo "이미 존재 — 기존 계정을 쓴다"
uvicorn app.main:app --port 8908 & sleep 3
curl -s -c /tmp/oa-session.txt -X POST localhost:8908/api/auth/login -H 'content-type: application/json' \
     -d "{\"username\":\"admin\",\"password\":\"$TEST_ADMIN_PW\"}" -i | grep -i "set-cookie"
#   → Set-Cookie가 나와야 한다. 안 나오면 아래가 전부 401이다
curl -s -b /tmp/oa-session.txt -X POST localhost:8908/api/search \
     -H 'content-type: application/json' -d '{"query":"정합성","k":5}' \
     -o /tmp/oa-search.json -w '%{http_code}\n'
#   → 200이어야 한다. 405면 메서드를, 401이면 로그인을 틀린 것이다
head -40 /tmp/oa-search.json; kill %1

# 10) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. **먼저 #12 티켓 본문을 정정한다** — `tsvector` 원안이 남아 있으면 근거가 오염된다.
   ⚠️ `gh issue edit --body-file`은 **빈 파일도 성공해 본문을 조용히 지운다.**
   현재 본문을 먼저 받아 두고 편집하라.
2. 위 AC 커맨드를 실행한다.
3. 아키텍처 체크리스트를 확인한다:
   - **`DISTINCT ON`이 융합 뒤에 있는가?** 앞에 있으면 UUID 순으로 잘린다 (ADR-011 보강 2)
   - **권한이 두 경로 모두에 걸렸는가?** 키워드 경로가 뚫리면 private **본문**이 매칭된다
   - **융합이 파이썬이 아니라 SQL인가?** 앱에서 합치면 이 기능의 논지가 사라진다
   - **후보 `LIMIT`이 `ef_search`보다 작은가?** 등호에서도 모자란다 (ADR-011 보강 4)
   - **융합이 실제로 나아졌는가?** 나빠졌으면 채택하지 않고 그 사실을 적는다.
     **"만들었으니 쓴다"로 가지 마라**
4. 결과에 따라 `phases/m9-wikilink-rrf/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 융합이 나아지지 않음 → `"status": "completed"`, `"summary": "미채택 — 측정 근거"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`to_tsvector`를 쓰지 마라.** 이유: 한국어에서 조사 때문에 0건이 나온다 (#29 실측)
- **두 결과를 애플리케이션에서 합치지 마라.** 이유: `CLAUDE.md` CRITICAL이고,
  **이 기능이 "같은 계층"의 증거가 되는 이유가 바로 단일 SQL**이다
- **`DISTINCT ON`을 융합 앞에 두지 마라.** 이유: ADR-011 보강 2
- **키워드 경로의 권한 필터를 빠뜨리지 마라.** 이유: private **본문**이 매칭되어 샌다 —
  벡터 경로보다 직접적인 누출이다
- **`gh issue edit --body-file`을 빈 파일로 실행하지 마라.** 이유: 성공하면서 본문을
  지운다. #38이 실제로 그렇게 날아갔다
- **측정 없이 채택하지 마라.** 이유: ADR-016이 "조건부 채택"이고 실측 전까지 불확실하다고
  스스로 적어 뒀다
- **그래프 확장(m7 step 8)을 건드리지 마라.** 이유: RRF는 **진입점 랭킹**을 바꿀 뿐이고,
  확장은 그 뒤에 그대로 붙는다
