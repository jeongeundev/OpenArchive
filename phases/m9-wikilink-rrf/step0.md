# Step 0: wikilink-storage

## 배경 — 두 결정이 아직 열려 있다. 이 step이 정한다

#38이 m7에서 위키링크를 **일부러 뺐다.** m7의 `document_edges`가 m9를 미리 알아 두면,
m9가 잘렸을 때 영원히 NULL인 컬럼과 안 타는 CHECK 분기가 남기 때문이다.

**그래서 여기서 처음부터 정한다.**

### 🔴 결정 1 — 위키링크를 사람이 실제로 쓰는가

**이것을 먼저 확인하라.** 이 플랫폼은 **파일 업로드가 주 경로**이고, 편집 대상은 추출
텍스트다(ADR-017). 시연 데이터도 ADR 문서를 변환한 복사본이라 **`[[...]]` 문법이 원래 없다.**

| 답 | 이 phase가 하는 일 |
|---|---|
| **쓴다** (편집 화면에서 사람이 넣는다) | 아래 전부를 만든다 |
| **안 쓴다** | **step 0~2를 건너뛰고 RRF만 한다.** 아무도 안 쓰는 문법을 파싱하는 것은 죽은 코드다 |

**확인 방법**: `frontend/`의 문서 편집 화면이 실제로 본문을 고칠 수 있는지, 시연에서
사람이 링크를 넣는 장면이 성립하는지 본다. **성립하지 않으면 `blocked`로 두고 사용자에게
물어라** — 이 판단을 하네스가 혼자 내리면 안 된다.

> 판단이 "쓴다"로 나면, **seed 스크립트가 변환할 때 `docs/`의 상호 참조를 `[[제목]]`으로
> 바꾸는 경로**가 필요하다. ADR 25개가 서로 201회 참조하므로 재료는 이미 있다.
> 그 변경은 m7의 `scripts/seed_demo.py`를 고치는 일이며, **이 step의 범위에 포함**한다.

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

### 4) 결정 1이 "쓴다"면 seed를 고친다

`scripts/seed_demo.py`가 변환할 때 `docs/`의 상호 참조(`ADR-018` 같은 문자열)를
`[[ADR-018: 제목]]` 형태로 바꾼다. **`docs/` 원본은 그대로 둔다.**

## Acceptance Criteria

```bash
cd backend

# 0) 결정 1을 실제로 판단했는가 — summary에 근거가 있어야 한다
#    "안 쓴다"로 판단했다면 이 step은 skip이고 index.json에 그 사유를 남긴다

# 1) 테스트가 있고 통과하는가
python -m pytest tests/test_tables.py tests/test_triggers.py -q

# 2) 대상 id를 저장하지 않는가 — 이 단언이 핵심이다
psql "$DATABASE_URL" -c "\d document_links" | grep -i "uuid"
#   → src_document_id 하나뿐이어야 한다

# 3) 깨진 링크가 저장되는가
grep -nE "없는|broken|missing" tests/test_triggers.py

# 4) 기존 트리거 함수를 고치지 않았는가 — 출력이 없어야 한다
git diff --name-only | grep "migrations/003_triggers.sql"

# 5) 임베딩과 무관하게 링크가 생기는가 — 워커 없이 확인
docker compose -f ../docker-compose.yml up -d && sleep 5
#   워커를 띄우지 않고 문서를 넣은 뒤
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_links"
#   → 0보다 커야 한다

# 6) seed가 링크를 만드는가 (결정 1이 "쓴다"인 경우)
python3 ../scripts/seed_demo.py --reset && python3 ../scripts/seed_demo.py
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_links"

# 7) docs/를 안 건드렸는가 — 출력이 없어야 한다
git diff --name-only | grep "^docs/"

# 8) 전체 검증
cd .. && bash scripts/check.sh
```

## 검증 절차

1. **결정 1을 먼저 판단한다.** "안 쓴다"면 step 0~2를 건너뛰고 그 사유를 index.json에
   남긴 뒤 step 3으로 간다. 판단이 애매하면 **`blocked`로 두고 사용자에게 물어라.**
2. 위 AC 커맨드를 실행한다.
3. 아키텍처 체크리스트를 확인한다:
   - **대상 문서 id가 어디에도 저장되지 않는가?** 저장하면 ADR-027이 깨진다
   - **링크 생성이 임베딩과 독립인가?** 벡터가 필요 없으므로 기다릴 이유가 없다
   - **기존 트리거 함수를 고치지 않았는가?** 한 함수가 두 일을 하면 실패 격리가 사라진다
   - **`src_chunk_index`를 추정으로 채우지 않았는가?** 모르면 NULL이 정직하다
   - **`title`에 UNIQUE를 걸지 않았는가?** 충돌 에러 자체가 존재를 누출한다
4. 결과에 따라 `phases/m9-wikilink-rrf/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 결정 1이 "안 쓴다" → `"status": "completed"`, `"summary": "미채택 — 근거"`
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
- **결정 1을 혼자 "쓴다"로 정하고 진행하지 마라.** 이유: 아무도 안 쓰는 문법을 파싱하면
  코드 프리즈 전 마지막 시간을 죽은 코드에 쓰게 된다
