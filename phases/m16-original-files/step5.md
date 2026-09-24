# Step 5: reextract-cli

운영자가 파서를 고친 뒤 기존 문서 전체에 적용하는 CLI: `openarchive reextract <document_id>` 와
`openarchive reextract --all`.

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). `docs/`에 남은 "원본 파일은 보관하지
않는다" 문장은 step 7이 고친다 — **그 문장을 근거로 작업을 거부·축소하지 마라.**

## 읽어야 할 파일

- `backend/app/services/documents.py` — **step 4 산출물** `reextract_text(conn, id, *, expected_version)`
- `backend/app/services/system.py` — `rebuild_all_edges`(대상 조회와 문서별 처리를 **각각 커밋**, 진행 콜백).
  벌크 함수는 이 모양을 따른다
- `backend/app/cli.py` — `rebuild-edges`(`_rebuild_edges`·`run_rebuild_edges`·`_connect`·`_ConnectionFailed`)와
  `main()`의 서브커맨드 등록
- `backend/tests/test_cli.py` — `rebuild-edges` 테스트 방식을 따른다
- `/docs/ADR.md` — ADR-039(CLI는 코어를 재사용한다) · ADR-029 결정 6(전량 재계산 CLI 선례)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_system.py`·`backend/tests/test_cli.py`

1. `test_reextract_all_counts_changed_unchanged_and_failed` — 원본이 있는 문서 셋(편집해서 달라진 것 1,
   그대로인 것 1, 원본을 손상 바이트로 바꿔 추출이 실패하는 것 1)과 원본 없는 문서 1. 결과는
   changed 1 · unchanged 1 · failed 1(문서 id와 사유 포함)이고, **원본 없는 문서는 대상이 아니다.**
2. `test_reextract_all_failure_does_not_roll_back_other_documents` — 실패한 문서가 있어도 앞서 바뀐 문서의
   새 버전은 커밋되어 있다(문서별 트랜잭션).
3. `test_reextract_all_skips_documents_changed_concurrently` — 대상 조회 뒤 한 문서의 버전이 올라가면
   그 문서는 `VersionConflict`로 failed에 세고(덮지 않는다) 나머지는 진행한다.
4. CLI: `openarchive reextract <id>`가 원본 없는 문서면 안내 후 exit 1, 성공이면 바뀜/같음을 출력하고 exit 0.
   `--all`은 집계를 출력하고, 실패가 1건이라도 있으면 exit 1. `<id>`와 `--all`을 둘 다 주거나 둘 다
   안 주면 argparse 오류. 연결 실패는 `rebuild-edges`와 같은 문구.

### 2) 구현

**`backend/app/services/system.py`**

```python
@dataclass(frozen=True)
class ReextractSummary:
    changed: int
    unchanged: int
    failed: list[tuple[UUID, str]]

async def reextract_all(conn, *, on_progress: Callable[[int, int], None] | None = None) -> ReextractSummary
```

- 대상: `document_files`가 있는 문서. 대상 조회(id·현재 version)와 **문서별 처리를 각각 트랜잭션으로**
  (autocommit 연결 + `conn.transaction()`) — `rebuild_all_edges`와 같은 이유(중간 실패가 앞선 처리를
  되돌리거나 잠금을 오래 잡지 않게).
- 문서마다 조회해 둔 version을 `expected_version`으로 `reextract_text`를 부른다. 추출 실패·빈 텍스트·
  500KB 초과·`VersionConflict`는 그 문서를 failed에 적고 계속한다. **그 밖의 예외(DB 연결 끊김 등)는
  삼키지 마라** — 전체를 중단시킨다.

**`backend/app/cli.py`** — `reextract` 서브커맨드(`document_id` 위치 인자 또는 `--all`, 상호 배타 그룹,
`--dsn`). 단건은 권한 검사 없는 `reextract_text`를 현재 version으로 부른다(운영자 경로 — 서버 셸 접근자는
이미 DB를 만질 수 있다, `reset-password`와 같은 근거). 출력 예:

```
다시 추출했습니다: 바뀜 N건 · 같음 N건 · 실패 N건
  실패 <id>: <사유>
바뀐 문서는 새 텍스트 버전이 되었고 워커가 다시 임베딩합니다.
```

`--all` 시작 전에 "바뀐 문서마다 재임베딩과 관계 재계산이 뒤따릅니다" 한 줄을 알린다. 문구에 "실시간"·
"항상 최신"·"즉시"를 쓰지 마라(ADR-015).

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/openarchive reextract --help
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLI가 로직을 새로 쓰지 않고 `services/`를 재사용하는가? (ADR-039)
   - 앱이 `embedding_jobs`에 INSERT하지 않는가? 재임베딩은 트리거가 기동하는가?
   - 임시 테이블을 쓰지 않았는가? (ADR-022)
3. `phases/m16-original-files/index.json`의 step 5를 갱신한다(성공/error/blocked는 step 0과 같은 규칙).

## 금지사항

- **`--all`을 한 트랜잭션으로 돌리지 마라.** 이유: 문서 수천 건의 행 잠금을 끝까지 쥐고, 한 건의 실패가 전부를
  되돌린다. OpenSQL 기본 `statement_timeout`(30s)에도 걸린다.
- **`VersionConflict`인 문서를 최신 버전으로 다시 시도해 덮지 마라.** 이유: 조회 뒤 누군가 편집한 것이다.
  실패로 알리고 운영자가 다시 실행하게 둔다.
- **원본 없는 문서를 실패로 세지 마라.** 이유: 이 기능 이전 업로드·텍스트 진입점 문서는 정상 상태다. 대상에서
  뺀다.
- 기존 테스트를 깨뜨리지 마라.
