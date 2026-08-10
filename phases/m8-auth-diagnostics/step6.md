# Step 6: diagnostics

> ⚠️ **이 step은 한 레이어를 넘는다** — 진단 서비스·API·화면을 함께 만든다.
> `harness.md`의 「하나의 step에서 하나의 레이어」에서 벗어나는 자리다. 쪼개지 않은 이유는
> 자르는 기준이 **「단독 시연이 성립하는가」**여서(#38), API만 있는 중간 step은 보여줄 것이
> 없기 때문이다. **커밋 스코프(`api`)가 변경 범위를 다 담지 못하므로 `summary`에 프론트
> 변경도 적는다.**

## 배경 — 관계가 저장돼 있으면 진단은 집계 쿼리다

#29의 2순위 첫 항목이다. m7이 관계를 이미 저장했으므로 **새 알고리즘 없이 집계 쿼리와
화면**으로 끝난다.

### ⚠️ #29가 적은 다섯 항목 중 둘은 이 phase에서 성립하지 않는다

| #29의 항목 | m8에서 |
|---|---|
| 고아 | ✅ edge가 하나도 없는 문서 |
| 중복 | ✅ `overlaps` 중 비율이 매우 높은 쌍 |
| 미분류 | ✅ 태그가 없는 문서 |
| **깨진 링크** | ⏭ **m9로 간다** — 위키링크가 없으면 깨질 링크 자체가 없다 |
| **빠진 연결** (가까운데 관계 없음) | ❌ **성립하지 않는다** — 컷오프가 순위 기반이라 상위 N개는 **항상** 관계가 생긴다. "가까운데 관계 없음"이라는 상태가 정의상 존재하지 않는다 |

**「빠진 연결」이 사라진 것을 조용히 넘기지 마라.** #29 시점에는 절대 임계값을 가정했고,
#38이 컷오프를 순위로 바꾸면서 이 항목이 무의미해졌다. **그 사실을 `summary`와
`docs/PRD.md`(진단 기능 서술이 있다면)에 적는다.**

### 열람 범위가 진단의 기준이다

**집계도 열람 범위 기준**이다(ADR-027 결정 3). *"사용자마다 고아 수가 다르다"*는 부작용이
아니라 **옳은 동작**이다 — 전체 기준으로 세면 *"고아가 아니다"*가 곧 *"내가 못 보는 이웃이
있다"*가 되어 **정의상 누출**이다.

## 읽어야 할 파일

- `docs/ADR.md` **ADR-027 결정 3** — 집계가 왜 열람 범위 기준인지
- `backend/app/services/visibility.py` — `VISIBLE_TO_USER`. **모든 진단 쿼리가 이걸 쓴다**
- `backend/app/services/related.py` — m7 step 7이 만든 edge 조회 방식
- `backend/migrations/006_edges_tables.sql` — `kind`와 `score`의 의미.
  **`score`는 척도가 종류마다 다르다**
- `frontend/src/app/admin/` — 기존 관리 화면. 진단은 여기 붙거나 별도 화면이다

## 작업

### 1) 테스트를 먼저 쓴다

`backend/tests/test_diagnostics.py`:

- **고아**: edge가 없는 문서만 나온다. edge가 있는 문서는 안 나온다
- **중복**: 같은 내용을 두 번 넣으면 목록에 뜬다
- **미분류**: 태그 없는 문서만 나온다
- **열람 범위**: 세 시선(익명·타인·소유자)에서 **개수가 다르다**.
  `tests/test_visibility.py`에 진단 호출부를 추가한다
- **private을 경유한 누출이 없는가**: 내가 못 보는 문서와만 이어진 문서는
  **나에게는 고아로 보여야 한다** — 이것이 ADR-027 결정 3의 실제 의미다

> **이 마지막 단언이 이 step의 핵심이다.** 빠뜨리면 고아 판정이 전역 집계가 되어
> 정의상 누출이 된다.

### 2) 진단 서비스

`backend/app/services/diagnostics.py` — **세 항목 각각의 집계 쿼리.**

- 전부 `VISIBLE_TO_USER`를 쓴다
- **중복 판정의 경계값**을 상수로 두고 근거를 적는다. `overlaps`의 `score`(매칭 비율)를
  쓰되, **다른 `kind`의 `score`와 섞어 비교하지 마라**
- `identical`(같은 `content_hash`)은 **중복 목록에 별도로 표시**한다 — 근사가 아니라
  확정이므로 같은 자리에 뭉치면 정확도가 흐려진다
- 각 항목은 **개수 + 목록 일부**를 준다. 전량을 주면 문서가 많을 때 응답이 터진다

### 3) API와 화면

| 엔드포인트 | 하는 일 |
|---|---|
| `GET /api/diagnostics` | 세 항목의 개수와 상위 목록 |

화면은 **항목별 카드 + 목록**이면 충분하다.

- 각 항목에 **무엇을 하라는 것인지** 한 줄로 적는다 (고아 → "관련 문서가 없습니다.
  태그를 달거나 다른 문서에서 참조해 보세요")
- **"문제"라고 단정하지 마라.** 고아 문서가 잘못된 것은 아니다. 진단이지 오류 목록이 아니다
- **개수가 0일 때 화면이 비지 않게** 한다 — "정리할 것이 없습니다"

## Acceptance Criteria

```bash
cd backend

# 1) 테스트가 있고 통과하는가
test -f tests/test_diagnostics.py
python -m pytest tests/test_diagnostics.py tests/test_visibility.py -q

# 2) 경유 누출 단언이 실제로 있는가 — 이 step의 핵심이다
grep -nE "고아|orphan" tests/test_diagnostics.py | head
grep -c "def test_" tests/test_diagnostics.py    # 5 이상

# 3) 모든 진단 쿼리가 권한 상수를 쓰는가
grep -c "VISIBLE_TO_USER" app/services/diagnostics.py   # 3 이상

# 4) 손으로 쓴 술어가 없는가 — 출력이 없어야 한다
grep -nE "visibility = 'public'" app/services/diagnostics.py

# 5) 실제 응답 — seed 데이터 위에서 두 시선의 개수가 다른가
#    쿠키는 로그인해서 파일로 받는다. 자리표시자를 손으로 채우지 마라
: "${TEST_ADMIN_PW:?step 1 픽스처에서 쓴 관리자 비밀번호를 환경변수로 넣고 실행하라}"
uvicorn app.main:app --port 8905 & sleep 3
curl -s localhost:8905/api/diagnostics | head -30              # 익명
curl -s -c /tmp/oa-session.txt -X POST localhost:8905/api/auth/login -H 'content-type: application/json' \
     -d "{\"username\":\"admin\",\"password\":\"$TEST_ADMIN_PW\"}" > /dev/null
curl -s localhost:8905/api/diagnostics -b /tmp/oa-session.txt | head -30   # 로그인한 시선
kill %1
#   → 두 응답의 개수가 달라야 한다

# 6) 프론트
cd ../frontend && npm run lint && npm run build && npm test

# 7) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **m7의 seed와 edge가 있어야 한다.**
2. 아키텍처 체크리스트를 확인한다:
   - **내가 못 보는 문서와만 이어진 문서가 나에게 고아로 보이는가?** 이 동작이 없으면
     고아 판정이 전역 집계이고, 그건 정의상 누출이다 (ADR-027 결정 3)
   - **세 시선에서 개수가 실제로 다른가?** 같다면 권한이 안 걸린 것이다
   - **`score`를 `kind` 넘어 비교하지 않았는가?** 척도가 다르다 (ADR-029)
   - **「빠진 연결」을 억지로 만들지 않았는가?** 순위 컷오프에서는 성립하지 않는다.
     빼고, 뺀 사실을 적는다
   - **「깨진 링크」를 m9에서 더할 자리가 남아 있는가?** 구조가 그것을 막으면 안 된다
3. 결과에 따라 `phases/m8-auth-diagnostics/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **전역 집계로 진단하지 마라.** 이유: *"고아가 아니다"*가 *"내가 못 보는 이웃이 있다"*를
  뜻하게 되어 정의상 누출이다 (ADR-027 결정 3)
- **「빠진 연결」 항목을 만들지 마라.** 이유: 순위 기반 컷오프에서 "가까운데 관계 없음"은
  존재하지 않는다. 억지로 만들면 거짓 항목이 된다
- **「깨진 링크」를 여기서 만들지 마라.** 이유: 위키링크가 m9다. 링크가 없으면 깨질 것도 없다
- **`🔒`나 "권한 없는 문서 N건"을 표시하지 마라.** 이유: ADR-027 결정 2
- **진단 결과를 "오류"로 부르지 마라.** 이유: 고아 문서는 잘못된 것이 아니다
- **전량 목록을 응답에 싣지 마라.** 이유: 문서가 많으면 응답이 터진다
