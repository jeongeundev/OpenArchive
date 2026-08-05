# Step 6: 임베딩 워커

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"워커 처리 루프" 절 전체**(5단계 SQL과 그 아래 인용 박스 두 개를 특히 정독하라), **"정합성 보장" 표**, "고가용성(HA) 전략"의 "애플리케이션이 담당하는 복구 로직"
- `/docs/ADR.md` — **ADR-009**(폴링이 주 경로 / LISTEN은 최적화), **ADR-004**(워커는 별도 프로세스), **ADR-001**(아웃박스·코얼레싱), **ADR-015**(보장 범위는 버전 일관성 + 최신 수렴)
- **이전 step 산출물**: `/backend/migrations/002_tables.sql`·`/backend/migrations/003_triggers.sql`, `/backend/app/services/chunking.py`, `/backend/app/embeddings/`(`get_provider`), `/backend/app/db.py`, `/backend/tests/conftest.py`(`migrated_db`), `/backend/tests/test_triggers.py`(DB 테스트 작성 방식)
- `/scripts/hooks/tdd-guard.sh` — `app/worker.py`는 `backend/tests/test_worker.py`를 요구한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

워커는 **"DB가 만들어 둔 잡을 집어가는 무상태 실행기"**다. 잡 생성·코얼레싱·삭제 정합성은 이미 DB가 보장하므로, 워커가 책임지는 것은 두 가지뿐이다.

1. 잡을 안전하게 집어가기 (`FOR UPDATE SKIP LOCKED`)
2. **커밋 직전에 "내가 읽은 내용이 아직 최신인가"를 확인하기** (`content_hash` 재확인)

2번이 멀티 워커 정합성의 핵심이다. 이것이 없으면 두 워커가 경쟁할 때 **낡은 버전이 최종 상태로 남을 수 있고**, 그러면 이 과제가 내세우는 "최신 수렴" 주장이 무너진다.

## 작업

### 1. `backend/app/worker.py`

아래 단위로 분해한다. **각 함수가 커넥션을 인자로 받는 형태를 유지하라** — 테스트가 두 개의 커넥션으로 경쟁 상황을 재현해야 한다.

```python
POLL_INTERVAL_SECONDS = 5.0
ZOMBIE_TIMEOUT_MINUTES = 5
MAX_ATTEMPTS = 3

@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    document_id: UUID

async def claim_job(conn) -> ClaimedJob | None
async def load_document(conn, document_id) -> tuple[str, str] | None   # (content, content_hash)
async def finalize_job(conn, job, expected_hash, chunks, vectors) -> bool
async def fail_job(conn, job, error) -> None
async def sweep_zombies(conn) -> int
async def process_once(conn, provider) -> bool     # 잡 하나 처리. 처리했으면 True
async def drain(conn, provider) -> int             # 잡이 없을 때까지 처리하고 건수 반환
async def run_worker() -> None                     # 폴링 루프 + LISTEN 최적화
def main() -> None                                 # python -m app.worker 진입점
```

#### `claim_job` — 집어가고 즉시 커밋

`ARCHITECTURE.md`의 claim SQL을 그대로 쓴다. `status='pending' AND next_attempt_at <= now()`, `ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED`, `attempts=attempts+1`, `started_at=now()`.

- 같은 트랜잭션에서 `documents.embedding_status`도 `'processing'`으로 바꾼다 (UI 표시용).
- **즉시 커밋한다.** 임베딩은 오래 걸리는데 트랜잭션을 열어두면 행 잠금이 유지되어 다른 워커가 막히고, `processing` 상태가 UI에 보이지도 않는다.

#### `load_document` — content와 content_hash만

- **`version`을 여기서 읽지 마라.** 이유: 본문이 `A → B → A`로 되돌아오면 `content_hash`는 원래대로 돌아오지만 `version`은 2 올라가 있다. 해시 재확인은 통과하는데 여기서 읽은 `version`은 낡은 값이 되어, 청크에 틀린 버전이 기록된다. `version`은 `finalize_job`의 `FOR UPDATE` 아래에서 읽는다.
- 문서가 이미 삭제됐으면 `None`을 반환한다.

#### `finalize_job` — 단일 트랜잭션 + 해시 재확인

```sql
BEGIN;
  SELECT content_hash, version FROM documents WHERE id = %(doc_id)s FOR UPDATE;
  -- 행이 없다 → 문서가 삭제됐다. 잡도 CASCADE로 사라졌다. 아무것도 하지 않는다
  -- content_hash가 다르다 → 이 결과는 낡았다. 청크를 쓰지 않고 잡만 done으로 마감한다
  DELETE FROM document_chunks WHERE document_id = %(doc_id)s;
  INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding) ...
  UPDATE documents SET embedding_status='ready' WHERE id = %(doc_id)s;
  UPDATE embedding_jobs SET status='done', finished_at=now() WHERE id = %(job_id)s;
COMMIT;
```

**반드시 지킬 것:**

- **`document_chunks.version`은 위 `FOR UPDATE`로 읽은 값으로 채운다.** 다른 어디에서 읽은 값도 쓰지 마라. 이 컬럼이 정합성 검증 쿼리(`c.version <> d.version`)와 `/admin/status` 카운터의 근거이며, **잘못 채우면 카운터가 영원히 0이거나 영원히 0이 아니게 되어 지표 자체가 무의미해진다.**
- 해시가 달라 폐기하는 경우에도 **잡은 `done`으로 마감한다.** 이미 새 `pending` 잡이 트리거로 만들어져 있으므로 최신 내용으로 다시 처리된다. 여기서 잡을 실패 처리하면 재시도 횟수만 소모한다.
- 청크 교체는 **`DELETE` + `INSERT`가 같은 트랜잭션**이어야 한다. 다른 세션이 중간 상태를 보면 "버전 일관성" 보장이 깨진다 (ADR-015).
- 반환값으로 **결과를 반영했는지(True) 폐기했는지(False)**를 구분하라. 테스트가 이것을 확인한다.

**벡터 바인딩**: 벡터는 `'[0.1,0.2,...]'` 형태의 문자열 리터럴로 만들어 `%s::vector`로 캐스팅해 넣는다. **`pgvector` 파이썬 패키지를 의존성에 추가하지 마라** — 이 phase에서 벡터를 DB에서 읽어 파이썬 객체로 되돌리는 코드는 없고, 쓰기만 하므로 문자열 캐스팅으로 충분하다.

#### `fail_job` — 지수 백오프

- `attempts`는 claim 시점에 이미 증가해 있다. `attempts >= MAX_ATTEMPTS`면 잡을 `'error'`로, `documents.embedding_status`도 `'error'`로 바꾸고 `last_error`를 기록한다.
- 아직 여유가 있으면 `status='pending'`으로 되돌리고 `next_attempt_at`을 지수 백오프로 미룬다(예: 2·4·8초). `last_error`도 기록한다.
- **`documents.embedding_status`를 `pending`으로 되돌리지 마라** — 재시도 대기 중에도 UI에는 처리 중으로 보이는 편이 정확하다. 상태를 되돌리는 것은 트리거의 책임이다.

#### `sweep_zombies` — 좀비 회수

- `status='processing'`이고 `started_at`이 `ZOMBIE_TIMEOUT_MINUTES`보다 오래된 잡을 `'pending'`으로 되돌리고, 회수한 건수를 반환한다.
- **`attempts`를 초기화하지 마라.** 이유: 매번 초기화하면 계속 죽는 잡이 영원히 재시도되어 `MAX_ATTEMPTS`가 무의미해진다.
- 워커가 죽거나 연결이 끊겨 `processing`으로 남은 잡을 되살리는 장치다. ADR-020의 "시연 가능한 항목"에 포함된다.

#### `process_once` / `drain`

- `process_once`: claim → load → 청킹 → 임베딩 → finalize. 예외가 나면 `fail_job`으로 처리하고 **워커를 죽이지 마라.** 잡이 없으면 `False`를 반환한다.
- 문서가 이미 삭제됐으면(`load_document`가 `None`) 잡을 `done`으로 마감하고 넘어간다 — 실패가 아니다.
- **임베딩 호출은 `asyncio.to_thread`로 감싸라.** `provider.embed`는 동기 CPU 바운드이고, 이벤트 루프를 막으면 LISTEN 연결이 함께 멈춘다.
- `drain`: 잡이 없을 때까지 `process_once`를 반복하고 처리 건수를 반환한다.

#### `run_worker` / `main`

- 기동 시 `sweep_zombies`를 1회 실행한 뒤 루프에 들어간다.
- **폴링이 주 경로다** — 매 `POLL_INTERVAL_SECONDS`마다 `drain`한다. 주기적으로 `sweep_zombies`도 돈다.
- **`LISTEN`은 최적화다.** 별도 커넥션에서 `LISTEN embedding_jobs`를 걸고, 알림이 오면 다음 폴링을 앞당긴다. **LISTEN 설정이나 수신이 실패해도 워커를 종료하지 마라** — 로그만 남기고 폴링을 계속한다. 이유: OpenProxy 경유 시 LISTEN 동작이 문서로 보장되지 않으며, 없어도 파이프라인이 동작해야 한다 (ADR-009).
- 잡 처리 커넥션은 `app.db.get_pool()`에서 빌리고, LISTEN은 별도 커넥션을 쓴다. **`app/db.py`를 수정하지 마라.**
- **`run_migrations`를 호출하지 마라.** 마이그레이션 실행 주체는 API 서버 하나다 (ADR-012). 스키마가 없으면 실패하는 것이 옳고, README에 "API를 먼저 기동"이 명시된다.
- `python -m app.worker`로 실행되어야 한다. `Ctrl-C`에 깔끔하게 멈춘다.
- 프로바이더는 `get_provider()`로 얻는다 — 설정(`EMBEDDING_PROVIDER`)을 따른다.

### 2. `backend/tests/test_worker.py` — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.** `migrated_db` 픽스처와 `FakeProvider`를 쓴다. 실제 컨테이너에서 검증하며 **Mock·SQLite로 대체하지 마라.**

문서는 **직접 INSERT**한다 — 트리거가 잡을 만들어주므로 테스트가 `embedding_jobs`에 손댈 필요가 없다.

최소 아래를 검증한다.

1. **claim** — pending 잡을 집어가면 `processing`·`attempts=1`·`started_at` 설정, `documents.embedding_status='processing'`. 잡이 없으면 `None`.
2. **예약 시간 존중** — `next_attempt_at`이 미래인 잡은 claim되지 않는다.
3. **`SKIP LOCKED` 경쟁** — 잡 2건을 준비하고 두 커넥션이 각각 claim하면 **서로 다른 잡**을 가져간다. 잡이 1건이면 한쪽만 성공하고 다른 쪽은 **대기하지 않고 즉시 `None`**을 받는다.
4. **정상 처리(happy path)** — 문서를 INSERT하면 트리거가 잡을 만들고, `drain` 후: 청크가 생성되고, 각 청크 내용이 `chunk_text` 결과와 일치하며, `embedding_status='ready'`, 잡은 `done`, **`document_chunks.version == documents.version`**이다.
5. **재임베딩은 교체다** — 처리 완료된 문서의 본문을 바꾸고 다시 처리하면 청크가 **누적되지 않고 교체**되며, 새 `version`이 기록된다.
6. **낡은 결과 폐기** — claim·load까지 한 뒤, **다른 커넥션에서 문서를 수정**하고 나서 `finalize_job`을 호출하면 `False`를 반환하고 **청크가 쓰이지 않는다.** 잡은 `done`이고, 트리거가 만든 새 `pending` 잡이 남아 있다.
7. **멀티 워커 수렴** — 6번을 확장한다. 워커A가 v1 내용으로 처리하는 사이 문서가 v2로 바뀌고 워커B가 v2 처리를 **먼저 끝낸 뒤**, 워커A가 finalize를 시도한다. 최종 상태의 청크는 **v2 내용**이어야 한다. **이것이 "낡은 청크가 최종 상태로 남지 않는다"의 직접 증명이며 이 step에서 가장 중요한 테스트다.**
8. **삭제 정합성** — claim 후 문서를 삭제하면 `finalize_job`(또는 `process_once`)이 **예외 없이** 끝나고, 청크·잡이 남지 않는다.
9. **재시도 백오프** — `embed`가 예외를 던지는 프로바이더로 처리하면 잡이 `pending`으로 돌아가고 `next_attempt_at`이 미래이며 `last_error`가 기록된다.
10. **재시도 소진** — 같은 실패를 `MAX_ATTEMPTS`번 반복하면 잡이 `'error'`, `documents.embedding_status`가 `'error'`가 된다.
11. **좀비 회수** — `processing`이고 `started_at`이 임계 시간보다 오래된 잡이 `sweep_zombies`로 `pending`이 되고 반환값이 1이다. 임계 이내의 잡은 건드리지 않는다.
12. **폴링만으로 동작** — `LISTEN`을 전혀 걸지 않은 상태에서 `drain`만으로 잡이 처리된다. ADR-009가 요구하는 성질이다.

> **시간 의존 테스트는 `sleep`으로 만들지 마라.** `started_at`이나 `next_attempt_at`을 원하는 값으로 직접 UPDATE해서 상황을 만들어라. 5분을 기다리는 테스트는 존재할 수 없다.

### 3. `README.md` 갱신

워커 실행 절차를 짧게 보강한다. **API 서버를 먼저 기동해야 스키마가 생긴다**(ADR-012)는 순서를 명시하라.

## Acceptance Criteria

```bash
docker compose up -d
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_worker.py -v
.venv/bin/pytest                  # 전체 통과

# 진입점이 실제로 동작하는지 (스키마가 이미 올라간 상태에서 몇 초 돌리고 Ctrl-C)
# EMBEDDING_PROVIDER=fake python -m app.worker

cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `document_chunks.version`이 `FOR UPDATE` 아래에서 읽은 값으로 채워지는가?
   - 커밋 직전 `content_hash` 재확인이 있는가? 다르면 결과를 폐기하는가?
   - 청크 `DELETE`+`INSERT`가 단일 트랜잭션인가?
   - 폴링이 주 경로이고 LISTEN 없이도 동작하는가? (ADR-009)
   - 워커가 `embedding_jobs`에 **INSERT**하지 않는가? (상태 UPDATE는 허용, 생성은 트리거의 책임 — `CLAUDE.md` CRITICAL)
   - 워커가 `run_migrations`를 호출하지 않는가? (ADR-012)
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **공개 함수 목록과 상수값(폴링 주기·좀비 임계·최대 시도), 그리고 멀티 워커 수렴 테스트를 어떻게 재현했는지**를 포함시켜라.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **워커에서 `embedding_jobs`에 INSERT하지 마라.** 이유: `CLAUDE.md` CRITICAL — 잡 생성은 트리거의 책임이다. 워커는 기존 잡의 상태만 바꾼다.
- **`document_chunks.version`을 `load_document` 시점의 값이나 `documents.version` 서브쿼리로 채우지 마라.** 이유: 잠금 밖에서 읽은 값은 `A → B → A` 시나리오에서 틀린다. 이 컬럼은 정합성 카운터의 근거다.
- **`content_hash` 재확인을 생략하거나 `FOR UPDATE` 없이 하지 마라.** 이유: 멀티 워커 경쟁에서 낡은 결과가 최종 상태로 남는다.
- **`LISTEN`을 주 경로로 만들지 마라.** LISTEN 실패 시 워커가 종료되어서도 안 된다. 이유: ADR-009 — OpenProxy 경유 동작이 보장되지 않는다.
- **워커에서 마이그레이션을 실행하지 마라.** 이유: ADR-012 — 실행 주체는 API 서버 하나다.
- **`app/db.py`·`app/config.py`·마이그레이션 SQL을 수정하지 마라.** 이유: 이 step은 워커만 추가한다. 스키마가 부족해 보이면 임의로 고치지 말고 `error`로 보고하라.
- **`pgvector` 파이썬 패키지나 `numpy`를 의존성에 추가하지 마라.** 이유: 벡터는 쓰기만 하므로 문자열 리터럴 + `::vector` 캐스팅으로 충분하다.
- **멀티호스트 DSN·`target_session_attrs`를 쓰지 마라.** 이유: ADR-006 — 재연결은 OpenProxy의 책임이다.
- **워커를 API 프로세스 안에서 백그라운드 태스크로 돌리지 마라.** 이유: ADR-004 — 별도 프로세스여야 장애 격리와 시연이 가능하다.
- **테스트에서 `time.sleep`으로 좀비·백오프 시간을 기다리지 마라.** 이유: 테스트가 느려지고 불안정해진다. 타임스탬프를 직접 UPDATE하라.
- **실패하는 테스트를 skip·주석 처리·삭제하지 마라.** 이유: `CLAUDE.md` 개발 프로세스 — 특히 7번(멀티 워커 수렴)은 이 phase의 핵심 주장이므로 우회하면 phase 자체가 무의미해진다.
- 기존 테스트를 깨뜨리지 마라.
