# Step 2: 트리거

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"자동 임베딩 파이프라인 (DB 계층)" 절의 "트리거" 부분 전체**(함수 본문과 `CREATE TRIGGER` 정의), "정합성 보장" 표, "API 설계" 절의 **"임베딩 실패 복구(`POST /api/documents/{id}/reembed`)"** 와 **"인라인 편집과 낙관적 동시성"**
- `/docs/ADR.md` — **ADR-001**(트랜잭셔널 아웃박스, 코얼레싱), **ADR-009**(폴링이 주 경로 / `pg_notify`는 최적화), **ADR-015**(보장 범위는 버전 일관성 + 최신 수렴)
- **이전 step 산출물**: `/backend/migrations/002_tables.sql`(테이블·파셜 유니크 인덱스), `/backend/tests/test_tables.py`(DB 테스트 작성 방식), `/backend/tests/conftest.py`(`migrated_db` 픽스처)
- `/scripts/hooks/tdd-guard.sh` — `003_triggers.sql`은 `backend/tests/test_triggers.py`를 요구한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

**이 step이 이 과제의 심사 핵심이다.** "임베딩 갱신이 DB 계층에서 자동 트리거되고 원본-벡터 정합성이 유지된다"는 주장이 여기서 코드가 된다. 애플리케이션은 `documents`만 UPDATE하고, 버전 이력 기록과 잡 생성은 전부 트리거가 같은 트랜잭션 안에서 수행한다.

## 작업

### 1. `backend/migrations/003_triggers.sql`

`ARCHITECTURE.md`의 트리거 절에 있는 `on_document_content_changed()` 함수와 `trg_documents_content_changed` 트리거를 **그대로** 작성한다. 네 단계를 임의로 재배치하거나 생략하지 마라.

1. `document_versions`에 이력 INSERT — `ON CONFLICT (document_id, version) DO NOTHING`
2. `documents.embedding_status`를 `'pending'`으로 전환 (이미 pending이면 건드리지 않음)
3. `embedding_jobs` INSERT — `ON CONFLICT DO NOTHING` (파셜 유니크 인덱스가 코얼레싱 수행)
4. `pg_notify('embedding_jobs', NEW.id::text)`

트리거 정의에서 반드시 지킬 것:

```sql
AFTER INSERT OR UPDATE OF content_hash ON documents
FOR EACH ROW
WHEN (pg_trigger_depth() = 0)
```

- **`AFTER`다.** `BEFORE`로 바꾸지 마라 — 아웃박스는 행이 확정된 뒤에 기록되어야 한다.
- **`UPDATE OF content_hash`다.** 모든 UPDATE에 발화시키지 마라. 제목·태그만 바꾼 UPDATE로 재임베딩이 돌면 낭비이고, 버전 이력이 오염된다.
- **`WHEN (pg_trigger_depth() = 0)`을 빼지 마라.** 2단계의 `UPDATE documents`가 트리거를 재귀 발화시키는 것을 막는 안전장치다.
- 각 단계 위에 **왜 그렇게 하는지** 주석을 남겨라. 이 SQL 파일은 심사에서 직접 읽히는 산출물이다.

> **`UPDATE OF content_hash`는 값이 바뀌지 않아도 SET 절에 컬럼이 언급되면 발화한다.** 이것이 PostgreSQL의 동작이며, `POST /api/documents/{id}/reembed`가 `UPDATE documents SET content_hash = content_hash`로 재임베딩을 유도하는 근거다 (`ARCHITECTURE.md` API 설계 절). 애플리케이션이 `embedding_jobs`를 직접 건드리지 않고도 잡을 만들 수 있는 유일한 경로이므로, 이 성질이 깨지지 않는지 테스트로 확인한다.

### 2. `backend/tests/test_triggers.py` — 먼저 작성한다

**SQL보다 테스트를 먼저 작성하라.** tdd-guard가 테스트 없는 `003_triggers.sql` 쓰기를 차단한다.

`migrated_db` 픽스처를 쓰고, 모든 검증은 실제 컨테이너에서 수행한다. 최소 아래를 검증한다.

1. **INSERT → 잡 1건 + v1 이력** — 문서를 INSERT하면 `embedding_jobs`에 `pending` 1건이 생기고 `document_versions`에 `version=1` 행이 기록된다. **이력이 INSERT 시점부터 남는다는 것이 이 설계의 특징이다.**
2. **본문 수정 → 새 이력 + 코얼레싱** — `version`을 올리고 `content`·`content_hash`를 바꾸는 UPDATE를 **연속 두 번** 실행하면, `document_versions`는 3행(v1·v2·v3)이 되지만 **`pending` 잡은 여전히 1건**이다. 이것이 DB 계층 코얼레싱의 증거다.
3. **본문 외 수정 → 발화 없음** — `title`이나 `tags`만 바꾸는 UPDATE는 잡도 이력도 만들지 않는다.
4. **상태 되돌림** — `embedding_status`를 `'ready'`로 바꿔둔 뒤 본문을 수정하면 다시 `'pending'`이 된다.
5. **재임베딩 유도(reembed 경로)** — `UPDATE documents SET content_hash = content_hash WHERE id = ...`를 실행하면 (a) 트리거가 발화해 새 `pending` 잡이 생기고, (b) `version`은 오르지 않으며, (c) `ON CONFLICT DO NOTHING` 덕분에 **이력이 중복되지 않는다**. 세 가지를 모두 확인하라.
6. **`pg_notify` 발행** — 별도 커넥션에서 `LISTEN embedding_jobs`를 걸어둔 뒤 문서를 INSERT·커밋하면 알림이 도착하고, payload가 문서 UUID다. **롤백하면 알림이 오지 않는다**는 것도 확인하라 — 커밋 시에만 발행되므로 유령 이벤트가 없다는 ADR-001의 주장이다.
7. **재귀 발화 없음** — 트리거 내부의 `UPDATE documents`가 트리거를 다시 돌리지 않는다. 문서 INSERT 한 번에 이력 1행·잡 1건만 생기는 것으로 확인한다.
8. **processing 중 재수정** — 잡을 `processing`으로 바꾼 상태에서 문서를 수정하면 **새 `pending` 잡이 생긴다**(파셜 유니크 인덱스는 pending만 막으므로). 이것이 "처리 중 재수정되면 새 잡이 생겨 최신 내용으로 다시 처리된다"는 워커 설계의 전제다.

> 6번의 `LISTEN` 테스트는 **로컬 컨테이너 직결**이라 검증할 수 있다. 실 클러스터에서는 OpenProxy를 경유해 동작이 보장되지 않으며, 그래서 워커는 폴링을 주 경로로 삼는다 (ADR-009). 이 테스트가 통과한다고 해서 "NOTIFY에 의존해도 된다"는 뜻이 아니다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_triggers.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 트리거가 `AFTER INSERT OR UPDATE OF content_hash` + `WHEN (pg_trigger_depth() = 0)`인가?
   - 네 단계가 모두 있고 `ON CONFLICT` 절이 설계대로인가?
   - 애플리케이션 코드(`app/`)에 `embedding_jobs` INSERT가 없는가? (`CLAUDE.md` CRITICAL)
   - 코얼레싱 테스트가 "연속 수정 후에도 pending 1건"을 실제로 확인하는가?
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **트리거·함수 이름과 검증한 동작 목록을 포함시켜라.** 워커 step이 이 동작을 전제로 한다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **애플리케이션 코드에서 `embedding_jobs`나 `document_versions`에 INSERT하지 마라.** 이유: `CLAUDE.md` CRITICAL. "원본-벡터 정합성이 DB 안에서 보장된다"가 이 과제의 심사 핵심이며, 앱이 직접 쓰면 그 주장이 무너진다. (테이블 제약을 검증하는 테스트 코드는 예외다.)
- **트리거 함수 안에서 임베딩을 만들거나 외부 호출을 하지 마라.** 이유: 잡은 "재임베딩이 필요하다"는 신호만 담는다. 페이로드를 담으면 코얼레싱과 멱등성이 깨진다 (`ARCHITECTURE.md` 설계 근거).
- **`pg_cron`을 쓰지 마라.** 이유: 번들되어 있지만 선택하지 않았다 — 잡 생성은 트리거가, 소비는 워커가 담당한다 (ADR-001 보강).
- **`AFTER`를 `BEFORE`로, `UPDATE OF content_hash`를 `UPDATE`로 바꾸지 마라.** 이유: 위 작업 절에 근거를 적었다.
- **`WHEN (pg_trigger_depth() = 0)`을 제거하지 마라.** 이유: 트리거 내부 UPDATE가 재귀 발화한다.
- **워커나 임베딩 코드를 만들지 마라.** 이유: 후속 step의 범위다.
- **`LISTEN`/`NOTIFY`를 파이프라인의 주 경로로 가정한 코드를 만들지 마라.** 이유: ADR-009 — OpenProxy 경유 시 동작이 보장되지 않는다.
- 기존 테스트를 깨뜨리지 마라.
