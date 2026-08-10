# Step 4: visibility-predicate-constant

## 배경 — ADR-027이 m7으로 넘긴 실행

ADR-027이 원칙을 정하고 **강제 수단까지 지정**했다.

> 권한 술어를 **SQL 조각 상수**로 하나 두고 모든 쿼리가 그것을 쓴다.
> **`grep`으로 강제하지 마라** — 검사 범위에 테스트나 규칙 문서가 들어가면 문자열을 쪼개
> 빠져나간다(M5에서 결함 3건의 원인). **쿼리별 행동 테스트**로 고정한다.

지금 같은 술어가 **세 곳에 손으로 복사돼 있다.**

| 자리 | 파일 |
|---|---|
| `SEARCH_SQL` | `backend/app/services/search.py:29` |
| `_NEIGHBOR_CTE` | `backend/app/services/related.py:30` |
| `IDENTICAL_SQL` | `backend/app/services/related.py:56` |

step 8이 그래프 순회 쿼리를 새로 만드는데, **네 번째 복사본이 생기기 전에** 상수를 세운다.

### ⚠️ 이 step은 일부러 2곳만 고친다

`_NEIGHBOR_CTE`는 **step 7이 통째로 지운다**(관련 문서가 edge 기반으로 바뀐다). 지금 고치면
사흘 뒤 사라질 코드를 손보는 것이다. 그래서 **구현은 2곳**(`SEARCH_SQL`·`IDENTICAL_SQL`)만
바꾸고, `_NEIGHBOR_CTE` 자리는 step 7이 새 쿼리를 쓰면서 상수를 쓴다.

**대신 테스트는 지금 네 호출부를 전부 덮는다.** 테스트는 쿼리 형태와 무관하게 *"익명에게
private가 보이는가"*를 묻기 때문에, step 7이 구현을 갈아엎어도 **그대로 살아남아 회귀를
잡는다.** 이것이 step 경계를 메우는 방법이다 — phase 검증이 다 통과해도 인접 step의
결합부는 아무도 안 보기 때문이다(M3에서 실제로 발생했다).

## 읽어야 할 파일

- `docs/ADR.md` **ADR-027** — 원칙과 강제 수단. 이 step이 그 실행이다
- `backend/app/services/search.py` — `SEARCH_SQL`의 술어 자리와
  `apply_vector_search_settings`. **권한 필터는 벡터 정렬 서브쿼리 *안*에 있어야 한다**
  (ADR-018 재개정) — 상수로 빼면서 밖으로 옮기지 마라
- `backend/app/services/related.py` — `IDENTICAL_SQL`과 `_NEIGHBOR_CTE`
- `backend/tests/conftest.py` — DB 픽스처와 문서 생성 헬퍼. 행동 테스트가 이 위에 선다
- `backend/tests/test_search.py` · `test_related.py` — 기존 권한 관련 단언이 있는지.
  있으면 새 테스트와 겹치지 않게 한다

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_visibility.py`

**한 파일에 모은다.** 흩어 두면 "이 쿼리는 덮였나"를 사람이 세야 한다.

픽스처: 문서 셋을 만든다.

- `pub` — `visibility='public'`, `owner_id='alice'`
- `priv_a` — `visibility='private'`, `owner_id='alice'`
- `priv_b` — `visibility='private'`, `owner_id='bob'`

**세 시선**(익명 `user_id=None` · 타인 `bob` · 소유자 `alice`)마다 **네 호출부**를 단언한다.

| 호출부 | 단언 |
|---|---|
| `search_documents` | `priv_a`가 익명·`bob`의 결과에 **없고**, `alice`에게는 **있다** |
| `find_related` | 위와 같다 (`pub`을 기준 문서로) |
| `suggest_tags` | `priv_a`에만 있는 태그가 익명·`bob`의 추천에 **없다** |
| `find_related`의 `identical` | 같은 `content_hash`인 private가 익명·`bob`에게 **안 보인다** |

> **개수도 단언하라.** *"목록에 없다"*만 보면 `🔒` 같은 자리 표시가 생겨도 통과한다.
> ADR-027 결정 2는 **자리도 남기지 않는다**이므로, 반환 길이가 시선에 따라 달라져야 한다.

**실제 컨테이너에서 돈다.** Mock·SQLite로 바꾸지 마라 (`CLAUDE.md` CRITICAL).

### 2) 상수를 만든다 — `backend/app/services/visibility.py`

```python
# 쿼리에 그대로 끼워 넣는 SQL 조각. 바인딩 이름은 %(user)s로 고정한다.
VISIBLE_TO_USER = "(d.visibility = 'public' OR d.owner_id = %(user)s)"
```

- **테이블 별칭을 `d`로 고정**하는 것이 가장 싸다. 호출부가 별칭을 맞추면 되고,
  포맷 문자열로 별칭을 주입하면 그 자체가 새 실수 표면이 된다
- 별칭이 다른 자리(`IDENTICAL_SQL`의 `o`)는 **별칭을 `d`로 바꿔 맞춘다** — 상수 쪽을
  유연하게 만들지 마라 (`CLAUDE.md`: 요청하지 않은 유연성)
- **모듈 docstring에 원칙 한 문장과 ADR-027 참조**를 적는다. 다음 사람이 이 파일을 열었을 때
  *"왜 상수인가"*가 거기 있어야 한다

### 3) 두 곳에 적용한다

`SEARCH_SQL`과 `IDENTICAL_SQL`이 `VISIBLE_TO_USER`를 쓰도록 바꾼다.

- **술어의 위치를 옮기지 마라.** `SEARCH_SQL`에서는 벡터 정렬 서브쿼리 **안**에 남아야 한다
  (ADR-018 재개정 — 밖으로 빼면 비공개 문서가 후보 자리를 차지해 손해만 본다)
- `IDENTICAL_SQL`의 `o` 별칭을 `d`로 바꾼다. **다른 의미 변화가 없어야 한다**
- `_NEIGHBOR_CTE`는 **건드리지 않는다** (step 7이 지운다)

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
test -f tests/test_visibility.py
python -m pytest tests/test_visibility.py -q

# 2) 세 시선 × 네 호출부가 실제로 단언됐는가 — 개수를 센다
grep -c "def test_" tests/test_visibility.py            # 8 이상
grep -c "suggest_tags" tests/test_visibility.py         # 1 이상
grep -c "identical" tests/test_visibility.py            # 1 이상

# 3) 상수가 생겼고 두 곳이 그것을 쓰는가
grep -n "VISIBLE_TO_USER" app/services/visibility.py
grep -n "VISIBLE_TO_USER" app/services/search.py
grep -n "VISIBLE_TO_USER" app/services/related.py

# 4) 손으로 쓴 술어가 남아 있지 않은가 — 구현부만 검사한다 (테스트·문서는 범위 밖)
grep -nE "visibility = 'public'" app/services/*.py app/routers/*.py | grep -v "visibility.py"
#   → 출력이 없어야 한다

# 5) 술어가 벡터 정렬 서브쿼리 밖으로 나가지 않았는가 — 눈으로 확인할 것
sed -n '/^SEARCH_SQL/,/^"""/p' app/services/search.py

# 6) _NEIGHBOR_CTE는 그대로인가 — 출력이 없어야 한다
git diff -U0 app/services/related.py | grep -E "^[+-].*_NEIGHBOR_CTE" | grep -v "^[+-][+-]"

# 7) 기존 테스트가 전부 살아 있는가 — 회귀 확인
python -m pytest -q

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **테스트가 구현보다 먼저 쓰였는가?** 하네스에서는 tdd-guard가 무력하므로
     (`execute.py`가 skip-permissions로 돈다) 순서를 스스로 지켜야 한다
   - **개수 단언이 있는가?** 없으면 자리 표시가 생겨도 통과한다 (ADR-027 결정 2)
   - **`SEARCH_SQL`의 술어가 서브쿼리 안에 그대로 있는가?** 밖으로 나가면 recall이 손해다
   - **grep으로 강제하지 않았는가?** AC 4번은 *구현부만* 검사한다 — 검사 범위에
     테스트 파일이 들어가면 문자열을 쪼개 빠져나가는 회피가 생긴다 (M5 결함 3건의 원인)
   - **`_NEIGHBOR_CTE`를 건드리지 않았는가?** step 7이 지울 코드다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`_NEIGHBOR_CTE`를 고치지 마라.** 이유: step 7이 edge 기반 쿼리로 통째로 교체한다.
  지금 손보면 사라질 코드를 다듬는 것이다
- **술어를 벡터 정렬 서브쿼리 밖으로 옮기지 마라.** 이유: 비공개 문서가 후보 자리를 차지해
  recall만 잃는다. 1차 실측의 "JOIN이 HNSW를 막는다"는 재측정에서 재현되지 않았다 (ADR-018 재개정)
- **상수에 별칭 주입이나 옵션을 넣지 마라.** 이유: 요청하지 않은 유연성이고, 그 자체가
  새로운 실수 표면이다. 호출부가 별칭을 맞춘다
- **grep을 강제 수단으로 쓰지 마라.** 이유: ADR-027이 명시적으로 기각했다
- **Mock·SQLite·인메모리로 테스트를 대체하지 마라.** 이유: `CLAUDE.md` CRITICAL
- **`document_edges`를 참조하지 마라.** 이유: 테이블이 아직 없다 (step 5)
