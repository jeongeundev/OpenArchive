# Step 0: wikilink-storage

## 배경 — 두 결정이 아직 열려 있다. 이 step이 정한다

#38이 m7에서 위키링크를 **일부러 뺐다.** m7의 `document_edges`가 m9를 미리 알아 두면,
m9가 잘렸을 때 영원히 NULL인 컬럼과 안 타는 CHECK 분기가 남기 때문이다.

**그래서 여기서 처음부터 정한다.**

### ✅ 결정 1 — **확정: 위키링크를 만든다** (2026-08-11 사용자 결정)

**이 결정은 이미 닫혔다. 다시 판단하지 말고, 물어보지도 말고, 아래대로 진행하라.**

원래 이 자리는 *"위키링크를 사람이 실제로 쓰는가"*를 묻는 열린 결정이었다. 사용자가
**전부 실행(step 0~5)**으로 확정했다. 확정 근거는 다음 셋이며, 이후 step이 이 근거를
전제로 삼는다:

- **편집 화면이 이미 있다** — `frontend/src/components/TextEditor.tsx`가 추출 텍스트를
  `textarea`로 고친다. 사람이 `[[`를 칠 수 있다는 조건은 충족된다
- **채택의 실제 값어치는 ADR-027을 화면에서 증명하는 데 있다** — seed에 private 4건
  (`ADR-006:` `ADR-018:` `ADR-023:` `ADR-027:`)이 있고, 그것을 가리키는 링크가 다른
  계정에게는 **"없는 문서"와 똑같은 깨진 링크**로 보인다. m8이 만든 권한 경계를 눈으로
  증명하는 가장 직접적인 장면이다
- **재료가 이미 있다** — `docs/`가 `ADR-011` 같은 문자열로 **241회** 서로 참조한다

따라서 **step 0~2를 건너뛰지 마라.** seed 변경도 이 step의 범위다(아래 4항).

### 🔴 결정 2 — 동명 문서가 여럿일 때 무엇을 가리키나

`documents.title`에 유일성 제약이 **없다.** 그리고 **걸 수도 없다** — UNIQUE는 내가 볼 수
없는 private 문서와도 충돌하고, **그 충돌 에러 자체가 "그 제목의 문서가 존재한다"를
알려준다**(ADR-027 위반).

ADR-027이 이미 절반을 정해 뒀다.

- **워커가 대상 id를 저장 시점에 굳히면 안 된다** — 같은 제목의 public·private가 둘 다 있을 때
  워커가 private를 고르면, 익명에게는 **볼 수 있는 동명 문서가 있는데도** 깨진 링크로 보인다
- **없는 문서를 가리키는 링크는 허용된다** — *"private 대상은 없는 문서와 똑같이 깨진 링크로
  보인다"*가 성립하려면 깨진 링크가 정상 상태여야 한다

**남은 것은 하나다: 열람 가능한 동명이 여럿일 때.** 권장은 **전부를 링크 대상으로 보이는
것**이다 — 숨기면 정보가 사라지고, 하나를 고르면 기준이 임의적이다. 다만 순회에서는
`refers`가 1:N이 되므로 **그 사실을 감안한다.** 다른 선택을 하려면 근거를 ADR-030(step 5)에 적어라.

## 읽어야 할 파일

- `docs/ADR.md` **ADR-027** — 조회 시점 resolve와 깨진 링크의 의미
- `docs/ADR.md` **ADR-029** — `document_edges`가 왜 위키링크를 안 담는지
- `backend/migrations/003_triggers.sql` — 본문 변경 트리거. 링크 파싱이 **같은 자리**에 붙는다
- `backend/migrations/006_edges_tables.sql` — 테이블 정의 스타일
- `scripts/seed_demo.py` — 결정 1이 "쓴다"면 여기가 바뀐다

## 작업

### 1) 테스트를 먼저 쓴다 — `test_tables.py` · `test_triggers.py`

- 본문에 `[[제목]]`이 있으면 링크 행이 생기는가
- **같은 링크가 두 번 있으면 한 번만 저장되는가**
- 본문을 고치면 링크가 **갈아끼워지는가**
- **없는 제목을 가리키는 링크도 저장되는가** (허용이다)
- 문서를 지우면 그 문서가 **낸** 링크가 사라지는가
- **대상 문서 id가 저장되지 않는가** — 이 단언이 ADR-027을 지킨다

### 2) `backend/migrations/010_links_tables.sql`

```sql
CREATE TABLE document_links (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  src_chunk_index int,              -- 어느 대목에서 가리켰나
  target_title    text NOT NULL,    -- id가 아니다. 굳히지 않는다
  PRIMARY KEY (src_document_id, target_title, COALESCE(src_chunk_index, -1))
);
```

**주석에 반드시 남길 것:**

- **왜 `target_document_id`가 아닌가** — ADR-027. 워커에 사용자 컨텍스트가 없고,
  굳히면 익명에게 볼 수 있는 동명 문서가 가려진다
- **왜 `document_edges`가 아닌가** — 대상 타입이 다르고, 생성 시점도 다르다
  (벡터가 필요 없어 임베딩을 기다릴 이유가 없다)
- **깨진 링크는 정상 상태다** — 위키에서는 오히려 기능이다

### 3) `backend/migrations/011_links_triggers.sql`

`content_hash` 변경 트리거에 붙인다 — **임베딩을 기다릴 이유가 없다.** 벡터가 필요 없으므로
`003_triggers.sql`과 같은 시점이 맞다.

- 기존 트리거 함수를 **고치지 말고 새 트리거를 더한다** — 한 함수가 두 일을 하면
  실패 격리가 사라진다
- 파싱은 정규식(`regexp_matches` 계열)으로 충분하다. **`[[` 안의 제목만** 뽑는다
- 문서의 기존 링크를 **지우고 다시 넣는다**(본문이 곧 진실)

> ⚠️ **청크 위치(`src_chunk_index`)를 어떻게 아는가.** 본문 파싱 시점에는 청크가 아직
> 없을 수 있다. **NULL로 두는 것이 정직하다** — 문서 단위 링크로 충분하고,
> 위치가 꼭 필요하면 청크 준비 후 채우는 경로를 별도로 정한다. **억지로 추정하지 마라.**

### 4) seed를 고친다 — **재임베딩까지가 이 step의 범위다**

`scripts/seed_demo.py`가 변환할 때 `docs/`의 상호 참조(`ADR-018` 같은 문자열)를
`[[ADR-018: 제목]]` 형태로 바꾼다. **`docs/` 원본은 그대로 둔다.**

- **제목은 헤딩 텍스트 전체다.** `split_sections`가 `ADR-018: 태그·유형 필터를 …` 같은
  헤딩 한 줄을 그대로 `title`로 쓴다. `[[ADR-018]]`만 쓰면 **전부 깨진 링크가 된다** —
  번호 → 제목 매핑을 만들어 완전한 제목으로 링크하라
- **깨진 링크를 일부러 남겨라** — 존재하지 않는 번호를 가리키는 링크가 하나도 없으면
  step 1·2가 만드는 깨진 링크 경로가 시연에서 한 번도 나타나지 않는다. private 4건을
  가리키는 링크는 소유자에게는 정상으로 보이므로 이 자리를 대신하지 못한다

> 🔴 **`--reset`은 벡터를 지운다. 이 step이 끝나기 전에 되살려라.**
>
> 현재 개발 DB는 **BGE-M3 실벡터**다 — 63문서 · 280청크 · `document_edges` 1294행
> (2026-08-11 직접 확인). `seed_demo.py --reset`은 문서를 지우므로 **청크와
> `document_edges`가 전부 사라진다.** 재적재 후 반드시 워커를 돌려 되살린다:
>
> ```bash
> cd backend && EMBEDDING_PROVIDER=local .venv/bin/python -m app.worker
> #   embedding_jobs의 pending이 0이 될 때까지 두고, 그 뒤 종료한다
> ```
>
> **`EMBEDDING_PROVIDER=local`을 생략하지 마라.** `app/config.py:19`의 기본값이 `fake`라
> 생략하면 **조용히 가짜 해시 벡터로 채워진다.** 에러도 경고도 나지 않는데, step 3의 RRF
> 측정과 m7의 관계 그래프가 전부 그 위에서 이뤄져 §14 기록이 통째로 무의미해진다.
> 판별법: 임의 청크의 `embedding`을 `FakeProvider().embed([content])[0]`과 비교해
> 최대오차가 `1e-6` 미만이면 fake, `0.6` 근처면 BGE-M3다

## Acceptance Criteria

```bash
cd backend

# 0) DSN과 인터프리터를 먼저 고정한다 — 이것을 빼면 아래 psql이 전부 헛돈다
#    셸에 DATABASE_URL이 설정돼 있지 않다(확인함). 빈 문자열로 psql을 부르면 호스트의
#    로컬 소켓에 붙어 `role "..." does not exist`로 죽고, 그 실패가 `| grep`에 먹혀
#    "출력 없음 = 통과"로 거짓 성립한다
export DATABASE_URL="${DATABASE_URL:-postgresql://openarchive:openarchive@localhost:5433/openarchive}"
PY=.venv/bin/python        # 시스템 python3에는 psycopg가 없다

# 1) 테스트가 있고 통과하는가
$PY -m pytest tests/test_tables.py tests/test_triggers.py -q

# 2) 대상 id를 저장하지 않는가 — 이 단언이 핵심이다
psql "$DATABASE_URL" -c "\d document_links" | grep -i "uuid"
#   → src_document_id 하나뿐이어야 한다. 행이 0줄이면 통과가 아니라 접속 실패를 의심하라

# 3) 깨진 링크가 저장되는가
grep -nE "없는|broken|missing" tests/test_triggers.py

# 4) 기존 트리거 함수를 고치지 않았는가 — 출력이 없어야 한다
git diff --name-only | grep "migrations/003_triggers.sql"

# 5) 임베딩과 무관하게 링크가 생기는가 — 워커 없이 확인
docker compose -f ../docker-compose.yml up -d && sleep 5
#   워커를 띄우지 않고 문서를 넣은 뒤
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_links"
#   → 0보다 커야 한다

# 6) seed가 링크를 만드는가
$PY ../scripts/seed_demo.py --reset && $PY ../scripts/seed_demo.py
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_links"
#   → 0보다 커야 한다

# 6-1) 깨진 링크가 실제로 섞여 있는가 — 둘 다 0보다 커야 한다
psql "$DATABASE_URL" -c "SELECT count(*) FILTER (WHERE d.id IS NOT NULL) AS resolved, \
       count(*) FILTER (WHERE d.id IS NULL) AS broken \
  FROM document_links l LEFT JOIN documents d ON d.title = l.target_title;"

# 6-2) 🔴 벡터를 되살렸는가 — --reset이 청크와 edges를 지웠다
EMBEDDING_PROVIDER=local $PY -m app.worker      # pending이 0이 되면 종료
psql "$DATABASE_URL" -c "SELECT (SELECT count(*) FROM document_chunks) AS chunks, \
       (SELECT count(*) FROM document_edges) AS edges, \
       (SELECT count(*) FROM embedding_jobs WHERE status='pending') AS pending;"
#   → chunks 200 이상 · edges 0 초과 · pending 0. 하나라도 어긋나면 이 step은 끝나지 않았다

# 6-3) fake 벡터로 채우지 않았는가 — 0.6 근처여야 한다. 1e-6 미만이면 fake다
$PY - <<'PY'
import sys; sys.path.insert(0, ".")
import numpy as np, psycopg
from app.config import get_settings
from app.embeddings.fake import FakeProvider
with psycopg.connect(get_settings().database_url) as c:
    content, emb = c.execute(
        "SELECT content, embedding FROM document_chunks WHERE embedding IS NOT NULL LIMIT 1"
    ).fetchone()
v = np.array(eval(emb) if isinstance(emb, str) else emb, dtype=float)
f = np.array(FakeProvider().embed([content])[0], dtype=float)
d = float(np.abs(v - f).max())
print(f"최대오차 {d:.6f} → {'FAKE — 재임베딩 실패' if d < 1e-6 else 'BGE-M3 정상'}")
assert d >= 1e-6, "fake 벡터다. EMBEDDING_PROVIDER=local로 다시 임베딩하라"
PY

# 7) docs/를 안 건드렸는가 — 출력이 없어야 한다
git diff --name-only | grep "^docs/"

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. **결정 1은 이미 닫혔다** — 위키링크를 만든다(2026-08-11 사용자 결정). 다시 판단하지
   말고 바로 작업에 들어간다.
2. 위 AC 커맨드를 실행한다.
3. 아키텍처 체크리스트를 확인한다:
   - **대상 문서 id가 어디에도 저장되지 않는가?** 저장하면 ADR-027이 깨진다
   - **링크 생성이 임베딩과 독립인가?** 벡터가 필요 없으므로 기다릴 이유가 없다
   - **기존 트리거 함수를 고치지 않았는가?** 한 함수가 두 일을 하면 실패 격리가 사라진다
   - **`src_chunk_index`를 추정으로 채우지 않았는가?** 모르면 NULL이 정직하다
   - **`title`에 UNIQUE를 걸지 않았는가?** 충돌 에러 자체가 존재를 누출한다
   - **재적재한 벡터가 BGE-M3인가?** AC 6-3이 통과해야 한다. fake면 이후 전부가 무의미하다
4. 결과에 따라 `phases/m9-wikilink-rrf/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 판단 불가 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`documents.title`에 UNIQUE를 걸지 마라.** 이유: 볼 수 없는 private과도 충돌하고,
  그 에러가 존재를 알려준다 (ADR-027)
- **대상 문서 id를 저장 시점에 굳히지 마라.** 이유: 같은 제목의 public·private가 있을 때
  익명에게 볼 수 있는 문서가 가려진다 (ADR-027, #35)
- **깨진 링크를 막지 마라.** 이유: 위키에서는 기능이고, ADR-027의 *"권한 없음과 구분
  불가능"*이 그 위에 선다
- **`document_edges`에 위키링크를 넣지 마라.** 이유: `dst_document_id`가 `NOT NULL`이고
  대상 타입이 다르다 (ADR-029)
- **`003_triggers.sql`의 기존 함수를 고치지 마라.** 이유: 실패 격리
- **`src_chunk_index`를 추정으로 채우지 마라.** 이유: 틀린 위치는 없는 위치보다 나쁘다
- **결정 1을 다시 열지 마라.** 이유: 2026-08-11에 사용자가 「전부 실행」으로 확정했다.
  여기서 되묻는 것은 phase를 통째로 멈추는 일이다
- **`EMBEDDING_PROVIDER`를 생략한 채 워커를 돌리지 마라.** 이유: 기본값이 `fake`라
  조용히 가짜 벡터가 들어가고, 에러가 없어 아무도 알아채지 못한다
