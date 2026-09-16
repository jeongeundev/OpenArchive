# Step 6: demo-corpus

## 배경 — 시연 코퍼스가 "모든 문서 1청크"를 강제하고 있다

`scripts/demo_corpus/`(가상 회사 사내 문서 64건, 부서 4종: 경영지원 27 = `hr/`+`finance/`,
고객지원 12 = `cs/`, 물류 13 = `logistics/`, 보안 12 = `security/`)는 모든 문서가 540~990자로
**1청크**다. `backend/tests/test_seed_demo.py::test_every_corpus_document_fits_in_one_chunk`가 이를
불변식으로 못 박았다 — 당시 008 트리거의 `overlaps` 판정은 분모가 자기 청크 수라 2청크 문서가
걸리기만 하면 2/2 = 1.0이 되어 오탐이 13쌍 남았기 때문이다.

step 0(`014_edges_triggers.sql`)이 판정을 **양쪽 비율**(자기 쪽·상대 쪽 모두 0.8 이상)과 문서당
상한 5건으로 바꿨으므로 그 편향은 사라졌다. 이제 시연 코퍼스도 **실 문서처럼 길이가 섞여** 있어야
새 규칙이 여러 청크 문서에서 성립함을 시연으로 보여줄 수 있다(#94 (c)).

또 하나 — 트리거는 처리 시점까지 들어온 문서만 후보로 보므로 순차 적재 결과가 순서에 따라
흔들린다. step 5가 만든 `rebuild_all_edges`(`backend/app/services/system.py`)를 **적재가 끝난 뒤
한 번** 호출하면 전체 코퍼스 기준으로 수렴한다. `seed_demo.py`가 그 호출을 맡는다.

### 닫힌 결정

- 부서마다 **3~4건**(경영지원은 `hr/` 2 + `finance/` 2), 총 **12~16건**을 **2~4청크**로 늘린다.
  `chunk_text`(1,000자 창·문단 경계·오버랩 150)에서 대략 2청크 ≈ 1,100~1,800자, 3청크 ≈ 1,900~2,600자,
  4청크 ≈ 2,700~3,400자다. 정확한 청크 수는 `chunk_text(document.content)`로 확인한다.
- 나머지 문서는 1청크로 **그대로 둔다**(≥ 30건). 길이가 섞여야 한다.
- 늘리는 방식은 **같은 문서를 절 단위로 더 쓰는 것**이다 — 같은 주제·같은 부서 어휘·같은 문체로
  절을 3~6개 더한다(예: 「환불 처리 기준」이면 「부분 환불」「환불 거절 사유」「분쟁 시 절차」「기록과
  보고」). 다른 부서 내용을 섞지 않는다. 기존 절과 위키링크는 지우지 않는다.
- 동일 텍스트 쌍 1개(`경비 정산 처리 기준`과 `경비 정산 처리 기준 (2024년판)` — 프런트매터 `title`로
  구분)는 **둘 다 같은 내용으로** 늘리거나 둘 다 그대로 둔다. `test_corpus_contains_one_identical_text_pair`가 지킨다.
- 비공개 문서(`visibility: private`) ≥ 3, 두 도메인 이상 — 그대로. 위키링크 깨진 대상은
  `재해복구 훈련 계획` 하나 — 그대로.

## 읽어야 할 파일

- `CLAUDE.md` — "원본 파일 / 문서 텍스트 / 텍스트 버전 구분", 시연 코퍼스 관련 규칙
- `docs/PRD.md` §2 사용 사례(C = 조직 규정·지침) — 코퍼스가 흉내 내는 대상
- `scripts/seed_demo.py` — **수정 대상.** `parse_seed_document`(프런트매터 규칙: `tags` 필수, `title`·`visibility` 선택), `seed_documents`, `wait_until_ready`, `run`
- `scripts/demo_corpus/**/*.md` — **수정 대상.** 파일 형식은 `---\ntags: 부서, 세부\n---\n# 제목\n\n본문`
- `backend/app/services/chunking.py` — `chunk_text`의 창·경계 규칙
- `backend/app/services/system.py` — step 5의 `rebuild_all_edges(conn, *, on_progress)`
- `backend/tests/test_seed_demo.py` — **수정 대상.** 기존 불변식 테스트 전부, `migrated_db` 픽스처
- `backend/tests/conftest.py` — `process_all_embedding_jobs(conn, provider)`
- `docs/UI_GUIDE.md`·`README.md`의 시연 절차 — 코퍼스 수·성질을 적어 둔 곳이 있으면 확인만(문서 갱신은 step 7)

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_seed_demo.py`

- `test_every_corpus_document_fits_in_one_chunk`를 **삭제**한다(그 docstring의 전제가 014로 사라졌다).
- 대신 `test_corpus_mixes_single_and_multi_chunk_documents`:
  - 부서(`tags[0]`) 4종 각각에 `len(chunk_text(content)) >= 2`인 문서가 **3건 이상**
  - 모든 문서가 `<= 4`청크
  - 1청크 문서가 **30건 이상**
  - docstring에 이유를 적는다: *014의 양쪽 비율·문서당 상한이 2청크 편향을 막았으므로 길이를 섞어
    새 규칙이 여러 청크 문서에서 성립함을 시연한다.*
- 기존 `test_corpus_covers_four_departments_at_measurable_scale`·`test_corpus_titles_are_unique`·
  `test_corpus_has_private_documents_in_more_than_one_domain`·`test_corpus_wikilinks_resolve_except_one_broken_target`·
  `test_corpus_links_cross_domains`·`test_corpus_contains_one_identical_text_pair`·
  `test_corpus_files_live_under_one_directory_per_domain`·DB 테스트 3개는 **그대로 통과**해야 한다.
- 새 DB 테스트 `test_seed_run_rebuilds_edges_after_embedding`:
  1. `seed_documents(conn, load_seed_documents())` → `process_all_embedding_jobs(conn, FakeProvider())`.
     이 시점에 **가장 먼저 만들어진 seed 문서**(`ORDER BY created_at, id LIMIT 1`)의 `src_document_id` 행은 0이다
     (첫 문서가 ready일 때 후보가 없었다).
  2. `monkeypatch`로 `DATABASE_URL`을 `migrated_db`로 두고(`get_settings.cache_clear()`가 필요하면 부른다)
     `await run(reset=False, timeout=10, owner="seed")`를 호출한다. 신규 0건, 이미 ready라 대기는 즉시 끝나고,
     `rebuild_all_edges`가 돈다.
  3. 그 첫 문서의 `src_document_id` 행이 **0보다 크다**.

### 2) 코퍼스 확장 — `scripts/demo_corpus/`

닫힌 결정대로 12~16건을 고르고 늘린다. 고를 때 기준: 각 부서에서 **주제가 뚜렷하고 절이 더 붙을
여지가 있는 문서**(규칙·기준·런북류). 파일명·제목·태그·`visibility`는 바꾸지 않는다.

### 3) `scripts/seed_demo.py`

`run`에서 `wait_until_ready`가 끝난 뒤 `rebuild_all_edges(conn, on_progress=…)`를 호출하고
요약 출력에 `관계 재계산 {n}건`을 더한다. 이유를 주석으로: *트리거는 처리 시점까지의 문서만
후보로 보므로 적재 순서에 따라 관계가 달라진다. 적재가 끝난 뒤 한 번 전체 기준으로 수렴시킨다.*
모듈 docstring의 "가상 회사 사내 문서" 설명에 길이가 섞여 있다는 한 줄을 더한다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/python -m pytest tests/test_seed_demo.py -q
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check . ../scripts/seed_demo.py
cd backend && .venv/bin/python -c "import sys; sys.path.insert(0, '..'); from scripts.seed_demo import load_seed_documents; from app.services.chunking import chunk_text; from collections import Counter; c = Counter(len(chunk_text(d.content)) for d in load_seed_documents()); print(sorted(c.items())); assert c[1] >= 30 and sum(v for k, v in c.items() if k >= 2) >= 12 and max(c) <= 4"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 마지막 명령은 `[(1, N1), (2, N2), (3, N3), …]` 분포를 출력하고 assert가 통과해야 한다.
2. 아키텍처 체크리스트:
   - 늘린 문서가 여전히 **한 부서·한 주제**인가? (다른 부서 어휘를 섞으면 덩어리가 섞인다)
   - 위키링크 대상이 전부 실존 제목인가(깨진 것은 `재해복구 훈련 계획` 하나)?
   - `seed_demo.py`가 `document_edges`에 직접 INSERT하지 않는가? (`rebuild_all_edges`만 호출)
3. 결과에 따라 `phases/m13-relation-rules/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (늘린 문서 수와 청크 분포를 담아라)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 문서를 **새로 추가하거나 삭제하지** 마라. 이유: 제목·위키링크·동일 텍스트 쌍·비공개 분포를 지키는 테스트가 그 위에 서 있다. 요청은 기존 문서의 길이다.
- 1청크 문서를 전부 없애지 마라. 이유: 길이가 섞여야 "여러 청크 문서에서도 성립"이 보이고, 1청크 문서가 `related`로 남는지도 시연 대상이다.
- 실 BGE-M3로 순도를 재고 코퍼스를 다듬는 반복을 이 step에서 하지 마라. 이유: 실측은 phase 뒤 사용자가 개발 DB·VM에서 한다. 이 step의 산출물은 길이 분포와 테스트다.
- `test_seed_demo.py`의 DB 테스트를 Mock으로 바꾸지 마라.
- 기존 테스트를 깨뜨리지 마라 (삭제를 지시한 하나 제외).
