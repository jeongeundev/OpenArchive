# Step 1: seed-repo-docs

## 배경 — 측정이 이 데이터 위에서 이뤄진다

이 step이 만드는 것은 시연 데이터이지만, **먼저 쓰이는 곳은 시연이 아니라 step 2의 측정**이다.
관계 판정 상수(이웃 수 N · `OVERLAP_RATIO` · `BROADER_MARGIN`)를 실데이터로 정해야 하는데,
그 데이터가 없으면 측정 자체가 성립하지 않는다. 그래서 seed가 측정보다 앞에 온다.

**합성 데이터로 대신하지 마라.** `CLAUDE.md`에 이미 적혀 있듯, 상관관계 없는 서브쿼리로 만든
벡터는 전 행이 같은 값이 되는데 **에러도 경고도 없다.** 그 상태에서 잰 상수는 통째로 무의미하다.

#29가 이 저장소 문서를 고른 이유는 **관계 7종이 전부 실제로 나타나기 때문**이다 —
긴 ADR 본문(이어짐), ADR 상호 참조 201회(참조), ADR-018 재개정(개정), `CLAUDE.md` ↔ ADR
(포괄/상세), 주제별 묶임(분류). 아무 샘플이나 넣으면 그래프가 밋밋해서 판정 로직을 시험할 수 없다.

> ⚠️ **`docs/`를 건드리지 마라.** 플랫폼에 넣을 **복사본만** 변환한다. `docs/`를 쪼개면
> 저장소 안의 문서 링크가 전부 깨지고, 이 phase의 나머지 step이 읽을 근거 문서가 사라진다.

## 읽어야 할 파일

- `backend/app/services/documents.py` — `create_document`의 시그니처. **서비스를 재사용한다**
  (`CLAUDE.md`: 비즈니스 로직은 services에 두고 스크립트·라우터는 재사용만)
- `backend/migrations/002_tables.sql` — `documents`의 컬럼과 `content_not_blank` CHECK
- `backend/migrations/003_triggers.sql` — 왜 `embedding_jobs`에 직접 INSERT하면 안 되는지.
  `documents`에 넣기만 하면 잡·버전 이력은 트리거가 만든다
- `scripts/demo_recovery.sh` — 스크립트가 DB에 접근하는 기존 방식과 환경변수 규약
- `docs/ADR.md` — 쪼갤 대상의 실제 구조(`### ADR-0NN: 제목` 헤딩)

## 작업

### 1) `scripts/seed_demo.py`를 만든다

저장소 문서를 읽어 **문서 단위로 쪼갠 복사본**을 `create_document`로 적재한다.

**쪼개는 단위**는 문서마다 다르다.

| 원본 | 단위 | 대략 |
|---|---|---|
| `docs/ADR.md` | `### ADR-0NN: 제목` 헤딩마다 1문서 | 25~28 |
| `docs/OPENSQL_RESEARCH.md` | `## N. 제목` 절마다 1문서 | 15~20 |
| `docs/ARCHITECTURE.md` | `## ` 절마다 1문서 | 10~15 |
| `docs/PRD.md` · `docs/UI_GUIDE.md` · `docs/PROJECT_CONTEXT.md` · `docs/SETUP_OPENSQL.md` | 통째로 1문서씩 | 4 |
| `CLAUDE.md` | 통째로 1문서 | 1 |

`title`은 헤딩 텍스트를 그대로 쓴다(`ADR-018: 관련 문서 추천은 ...`). `content_type`은 `md`.

**태그를 반드시 단다.** 분류 관계(태그 공유)와 `suggest_tags`가 이 데이터 위에서 돌아야 하고,
step 9의 화면도 태그를 보인다. 원본 파일과 주제로 2~3개씩 — 예: `["adr", "검색"]`,
`["조사", "openproxy"]`. **태그를 안 달면 step 7의 태그 추천이 빈 결과만 낸다.**

**`visibility`를 섞는다.** 전부 `public`이면 ADR-027의 열람 범위 동작을 이 데이터로 볼 수 없다.
최소 3~5개를 `private` + 특정 `owner_id`로 넣고, **그중 하나는 다른 문서가 참조하는 문서**여야
한다(순회가 private 노드에서 멈추는지 확인할 수 있어야 한다).

### 2) 재실행 안전하게 만든다

같은 `title`이 이미 있으면 건너뛰거나 갱신한다. 두 번 돌려서 문서가 두 배가 되면
측정값이 통째로 흔들린다. **`--reset` 옵션으로 seed가 넣은 것만 지우는 경로**를 둔다
(사용자가 직접 올린 문서를 지우면 안 되므로 식별 수단이 필요하다 — `owner_id`를
`seed`로 고정하는 것이 가장 싸다).

### 3) 임베딩 완료를 기다리는 수단을 둔다

적재는 즉시 끝나지만 청크는 워커가 만든다. 스크립트가 **`embedding_status`가 전부 `ready`가
될 때까지 폴링하고 결과를 출력**한다 — 문서 수 · 청크 수 · 소요. step 2가 이 출력을 근거로 쓴다.

> 워커가 안 떠 있으면 영원히 기다린다. **타임아웃을 두고, 초과하면 "워커가 떠 있는지
> 확인하라"는 메시지와 함께 non-zero로 종료**한다.

### 4) 테스트

`backend/tests/test_seed.py`를 **먼저** 쓴다. 실제 컨테이너에 적용된 스키마 위에서 검증한다
(Mock 금지 — `CLAUDE.md`).

- 쪼개기 함수가 `docs/ADR.md` 문자열에서 **ADR 개수만큼** 문서를 만드는가
- 각 조각의 `content`가 `content_not_blank` CHECK를 통과하는가 (빈 절이 섞이면 적재가 터진다)
- `title`이 헤딩에서 정확히 뽑히는가
- **재실행해도 문서 수가 늘지 않는가**
- private로 지정한 문서가 실제로 `visibility='private'`로 들어가는가

> 쪼개기 로직은 파일 IO 없이 **문자열 → 문서 목록** 함수로 분리해야 테스트가 쉽다.
> 적재 부분만 DB를 탄다.

## Acceptance Criteria

```bash
# 1) 테스트가 먼저 있고 통과하는가
test -f backend/tests/test_seed.py
cd backend && python -m pytest tests/test_seed.py -q

# 2) 실제로 적재되는가 — 컨테이너가 떠 있고 워커가 도는 상태에서
python3 scripts/seed_demo.py --reset
python3 scripts/seed_demo.py

# 3) 적재 결과가 측정에 쓸 만한 규모인가
psql "$DATABASE_URL" -c "SELECT count(*) FROM documents WHERE owner_id = 'seed'"
#   → 50 이상이어야 한다. 20 미만이면 쪼개기가 안 먹은 것이다
psql "$DATABASE_URL" -c "SELECT count(*) FROM document_chunks"
#   → 200 이상

# 4) 벡터가 퇴화하지 않았는가 — CLAUDE.md가 경고한 자리다
psql "$DATABASE_URL" -c "SELECT count(DISTINCT embedding::text), count(*) FROM document_chunks"
#   → 두 값이 거의 같아야 한다. 앞이 1이면 측정 전체가 무의미하다

# 5) 권한이 섞여 있는가
psql "$DATABASE_URL" -c "SELECT visibility, count(*) FROM documents WHERE owner_id='seed' GROUP BY 1"
#   → private가 3 이상

# 6) 태그가 붙었는가
psql "$DATABASE_URL" -c "SELECT count(*) FROM documents WHERE owner_id='seed' AND cardinality(tags)=0"
#   → 0이어야 한다

# 7) docs/를 건드리지 않았는가 — 출력이 없어야 한다
git diff --name-only | grep "^docs/"

# 8) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **DB 컨테이너와 워커가 떠 있어야 한다** — 없으면
   `docker compose up -d`와 `python -m app.worker`를 먼저 띄운다.
2. 아키텍처 체크리스트를 확인한다:
   - **`embedding_jobs`에 직접 INSERT하지 않았는가?** (`CLAUDE.md` CRITICAL)
     `documents`에 넣기만 하면 트리거가 전부 처리한다
   - **`create_document` 서비스를 재사용했는가?** 스크립트가 SQL을 직접 쓰면
     services 재사용 규칙을 어긴다
   - **AC 4번의 `count(DISTINCT embedding::text)`를 실제로 확인했는가?** 이 한 줄이
     이후 모든 측정의 전제다
   - 재실행이 안전한가? 두 번 돌려 문서 수가 같은지 직접 확인한다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요(컨테이너·워커 부재 등) → `"status": "blocked"`,
     `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`docs/` 아래 파일을 수정·분할·이동하지 마라.** 이유: 저장소 문서 링크가 전부 깨지고,
  이 phase의 나머지 step이 읽을 근거가 사라진다. 읽어서 **복사본을 만들 뿐**이다
- **합성 벡터나 무작위 임베딩으로 대체하지 마라.** 이유: 전 행이 같은 벡터가 되어도
  에러가 안 나고, step 2의 측정이 통째로 무의미해진다 (`CLAUDE.md`)
- **`embedding_jobs`·`document_versions`에 직접 INSERT하지 마라.** 이유: 트리거의 일이다
  (`CLAUDE.md` CRITICAL)
- **`document_edges`를 만들거나 참조하지 마라.** 이유: 테이블이 아직 없다 (step 5)
- **태그 없이 적재하지 마라.** 이유: 분류 관계와 태그 추천이 이 데이터 위에서 돌아야 한다
- **전부 `public`으로 넣지 마라.** 이유: ADR-027의 동작을 시험할 데이터가 없어진다
