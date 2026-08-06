# Step 4: demo-recovery

## 배경 — 이 스크립트가 증명하는 것과 증명하지 않는 것

`PROJECT_CONTEXT.md`의 핵심 구현 목표 중 하나가 고가용성 활용이다. 그런데 사무국이
**Single 구성**을 지시했고(ADR-020), 노드가 1대라 승격할 replica가 없다.

| | Single 구성에서 |
|---|---|
| ✅ 연결 끊김 후 자동 재연결, 미처리 잡 무손실 재개, 좀비 회수, 정합성 수렴 | **이 스크립트가 증명한다** |
| ⛔ Patroni 리더 선출·승격, failover 소요 시간 | **증명하지 않는다. 할 수 없다** |

**"failover를 시연했다"고 쓰지 마라.** 하지 않았다. 정확한 표현은 ADR-020 결정 4에 있다.

증명 대상은 **애플리케이션이 담당하는 복구 로직**이며, 그 코드는 이미 전부 존재한다.
이 스크립트는 새 기능을 만드는 것이 아니라 **기존 경로를 실제로 태우고 관측 가능하게 찍는 것**이다.

| 이미 존재하는 복구 코드 | 하는 일 |
|---|---|
| `app/worker.py`의 `run_worker` 루프 `except Exception` | DB가 죽어도 워커 프로세스가 죽지 않고 다음 폴링에서 재시도 |
| `app/worker.py`의 `sweep_zombies` | `processing`으로 얼어붙은 잡을 `pending`으로 회수 |
| `app/worker.py`의 `_listen_for_jobs` 백오프 | LISTEN이 끊기면 경고만 남기고 재등록. 폴링이 주 경로라 파이프라인은 계속 돈다 (ADR-009) |
| `app/api/retry.py` | 읽기 요청 1회 재시도 |
| `app/db.py`의 풀 `check=check_connection` | 대여 시점의 죽은 커넥션을 걸러낸다 |
| `embedding_jobs` 테이블 | 트랜잭셔널 아웃박스 — DB 재기동 후에도 미처리 잡이 남아 있다 (ADR-001) |

## 읽어야 할 파일

- `docs/ADR.md` — **ADR-020**(Single 구성 대응, 특히 결정 3의 시연 가능/불가능 표와 결정 4의 표현 규칙),
  **ADR-009**(폴링 주 경로), **ADR-001**(트랜잭셔널 아웃박스), **ADR-012**(마이그레이션은 API만 실행)
- `docs/ARCHITECTURE.md` — **"책임 분리 — 무엇을 OpenSQL이 하고, 무엇을 우리가 하는가"** 표와
  그 아래 **"애플리케이션이 담당하는 복구 로직"** 절
- `backend/app/worker.py` — `claim_job`(선점을 **즉시 커밋**한다), `sweep_zombies`,
  `run_worker`의 루프 구조와 로그 문구
- `backend/app/api/documents.py` — 업로드는 **multipart**(`file` 필드, `X-User-Id` 헤더 필수),
  편집은 `PUT /api/documents/{id}`에 `{content, version}` JSON
- `backend/app/api/system.py` — `GET /api/system/status`가 주는 필드
- `backend/app/config.py` — 직전 step에서 추가된 `zombie_timeout_minutes`
- `docker-compose.yml` — 로컬 DB 서비스명은 `db`, 호스트 포트는 **5433**
- `README.md` — "빠른 시작"의 기동 순서. **API를 워커보다 먼저 띄워야 한다**(마이그레이션이
  API startup에서만 돌기 때문, ADR-012)

## 작업

`scripts/demo_recovery.sh`를 새로 만든다. 실행 가능해야 한다(`chmod +x`).

### 설계 제약 (지켜야 할 것)

**1) DB 정지·재기동 명령을 환경변수로 주입받는다.**

```bash
DB_STOP_CMD="${DB_STOP_CMD:-docker compose stop db}"
DB_START_CMD="${DB_START_CMD:-docker compose start db}"
```

이유: 로컬 pgvector 컨테이너와 실 OpenSQL VM 양쪽에서 같은 스크립트를 써야 한다. VM에서는
호출자가 `DB_STOP_CMD="ssh ... 'sudo systemctl stop ...'"` 형태로 주입한다. **스크립트 본문에
docker를 하드코딩하지 마라** — 기본값으로만 둔다.

**2) DB 조회에 `psql`을 쓰지 마라. `backend/.venv/bin/python`으로 조회하는 셸 함수를 둔다.**

이유: `psql`이 개발 머신에 없을 수 있고, `docker exec`로 컨테이너 psql을 쓰는 방식은 VM 모드에서
동작하지 않는다. `psycopg`는 이미 백엔드 의존성이므로 **두 환경 모두에서 유일하게 확실한 경로**다.

**3) 스크립트가 API와 워커를 직접 기동하고, `trap`으로 정리한다.**

이유: 이미 떠 있는 프로세스를 쓰면 로그를 캡처할 수 없고 프로세스 생존을 확인할 수 없다.
로그는 임시 파일로 받고, 검증에 그 내용을 grep한다.

`trap`은 **어떤 경로로 종료하든**(성공·실패·Ctrl-C) 다음을 보장해야 한다:
- 워커·API 프로세스 종료
- **DB를 다시 켠다** — 스크립트가 DB를 정지시킨 구간에서 실패하면 개발 환경이 망가진 채 남는다
- 데모가 만든 문서 삭제 (`DELETE /api/documents/{id}`, 삭제는 DB가 살아 있어야 하므로 순서 주의)

**4) 각 단계는 assert하고, 어긋나면 즉시 `exit 1`한다.**

이유: 이 스크립트가 곧 검증이다. 실패를 로그로만 남기고 0으로 끝내면 "재현 가능하게 동작한다"는
완료 조건을 만족하지 못한다.

**5) 대기는 반드시 타임아웃과 함께 폴링한다.** 무한 대기 금지. 타임아웃 시 현재 상태를 출력하고 실패.

### 시나리오 A — DB 정지 후 복구

```
1. 문서 2건 업로드 → 임베딩 완료 대기 → baseline 기록
   (정합성 카운터 0, 문서 수, 청크 수)

2. 워커를 SIGSTOP으로 멈춘다
   이유: 편집 직후 워커가 잡을 집어가면 "pending이 쌓였다"를 관측할 수 없다.
   SIGKILL이 아니라 SIGSTOP인 이유는 임베딩 프로바이더가 local일 때 재기동마다
   BGE-M3(4.3GB)를 다시 로드하기 때문이다.

3. 문서 1건 편집 (PUT /api/documents/{id})
   assert: jobs.pending == 1
   assert: inconsistent_documents == 1     ← 원본 버전이 올랐고 청크는 아직 이전 버전

4. DB 정지 ($DB_STOP_CMD)

5. 워커 SIGCONT
   assert: 워커 PID가 살아 있다 (kill -0)          ← run_worker의 except가 잡아준 결과
   assert: 워커 로그에 "처리 루프 실패" 가 나타난다   ← 타임아웃 폴링으로 대기
   assert: POST /api/search 가 비-2xx로 실패한다    ← 숨기지 않는다. ADR-020의 "짧은 중단"

6. DB 재기동 ($DB_START_CMD) → 접속 가능해질 때까지 대기

7. assert: jobs.pending 이 0으로 수렴한다          ← 코드를 한 줄도 안 건드리고 재개된다
   assert: inconsistent_documents == 0
   assert: 문서 수가 baseline과 같다               ← 잡 유실 0
```

### 시나리오 B — 워커 강제 종료 후 좀비 회수

```
1. 워커 SIGSTOP → 문서 여러 건 편집 → pending 적재 확인

2. 워커 SIGCONT → jobs.processing >= 1 이 될 때까지 짧은 간격으로 폴링

3. processing을 관측한 즉시 워커에 SIGKILL
   assert: jobs.processing >= 1 인 채로 남아 있다
   근거: claim_job이 선점을 즉시 커밋하므로(worker.py 참조) 프로세스가 죽어도 상태가 남는다

4. ZOMBIE_TIMEOUT_MINUTES=0 으로 워커를 재기동
   assert: 워커 로그에 "좀비 잡 .* 회수" 가 나타난다
   assert: jobs.pending·processing 이 0으로 수렴, inconsistent_documents == 0
```

**3번의 타이밍 레이스를 어떻게 다룰지가 이 시나리오의 난점이다.** `EMBEDDING_PROVIDER=fake`
(기본값)에서는 처리가 매우 빨라 `processing` 구간이 짧다. 다음 두 가지로 확률을 높여라:

- **편집 문서 수를 늘린다**(예: 5~10건). 큐가 길면 워커가 순차 처리하므로 `processing` 구간이
  전체 시간의 대부분을 차지한다
- **본문을 길게 만들어 청크 수를 늘린다.** 임베딩 시간이 청크 수에 비례한다

그래도 못 잡으면 **실패로 보고하고 `exit 1`하라.** 조용히 건너뛰거나 SQL로 `processing` 상태를
직접 만들어내지 마라 — 후자는 복구를 증명하는 것이 아니라 연출하는 것이다.

`ZOMBIE_TIMEOUT_MINUTES=0`이 안전한 이유: `run_worker`의 루프가 `sweep → drain → 대기`를
**순차로** 돌기 때문에, 워커가 자기가 처리 중인 잡을 다음 스윕에서 좀비로 판정할 수 없다.
데모는 워커 1대다.

### 출력 형식

사람이 읽고 그대로 결과보고서에 붙일 수 있어야 한다. 단계마다 무엇을 확인했는지와 실제 수치를
출력하라(예: `pending 1 → 0`, `inconsistent 1 → 0`). 색상·유니코드 장식은 최소한으로 한다.

마지막에 **이 데모가 증명하지 않은 것**(Patroni 승격)을 한 줄로 명시하고 끝내라.

## Acceptance Criteria

```bash
# 0) 전제: 로컬 DB가 떠 있고 backend/.venv가 준비되어 있어야 한다
docker compose up -d
cd backend && python3 -m venv .venv 2>/dev/null; .venv/bin/pip install -e ".[dev]" -q; cd ..

# 1) 실행 권한
test -x scripts/demo_recovery.sh

# 2) 실제로 끝까지 통과해야 한다 — 이것이 이 step의 핵심 AC다
bash scripts/demo_recovery.sh
echo "exit=$?"          # 0 이어야 한다

# 3) 실패를 실제로 감지하는지 — 존재하지 않는 정지 명령을 주면 실패해야 한다
DB_STOP_CMD="false" bash scripts/demo_recovery.sh
echo "exit=$?"          # 0이 아니어야 한다. 0이면 assert가 무력하다는 뜻이다

# 4) 뒷정리가 되었는지 — 데모 후에도 DB가 살아 있어야 한다
docker compose ps db

# 5) 금지 문구가 들어가지 않았는지 (출력 없어야 함)
grep -nE "failover를 시연|무중단|항상 최신|실시간 동기화" scripts/demo_recovery.sh

# 6) 전체 검증
bash scripts/check.sh
```

> **AC 2번과 3번의 실행 로그를 반드시 남겨라.** `.sh` 파일은 `scripts/hooks/tdd-guard.sh`의
> 검사 대상이 아니다(`.py`·`.ts` 계열만 매핑된다). 즉 이 step은 훅의 보호를 받지 못하며,
> **실제 실행만이 유일한 검증**이다. 실행하지 못했다면 통과로 처리하지 말고 그 사실과 이유를
> summary에 적어라.

## 검증 절차

1. 위 AC 커맨드를 실행한다. **특히 2번과 3번은 반드시 실제로 돌린다.**
2. 아키텍처 체크리스트를 확인한다:
   - ADR-020 결정 4의 표현 규칙을 지켰는가? ("failover를 시연했다"고 쓰지 않았는가)
   - DB 정지·재기동 명령이 환경변수로 주입 가능한가? docker를 본문에 하드코딩하지 않았는가?
   - `psql` 의존이 없는가?
   - `trap`이 실패 경로에서도 DB를 되살리는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/m5-recovery-demo/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`processing` 상태나 좀비 잡을 SQL로 직접 만들어내지 마라.** 이유: 그것은 복구를 증명하는
  것이 아니라 연출하는 것이다. 상태는 실제 워커의 동작으로만 만들어져야 한다.
  (테스트 코드에서 `started_at`을 미는 것은 단위 테스트의 관례이고, 데모는 성격이 다르다)
- **애플리케이션 코드를 고치지 마라** (`backend/app/**`, `frontend/src/**`). 이유: 복구 로직은
  이미 전부 존재한다. 데모를 통과시키려고 애플리케이션을 고치는 것은 인과가 뒤집힌 것이다.
  정말로 코드 결함을 발견했다면 고치지 말고 **`status`를 `blocked`로 두고 사유에 적어라**
- **마이그레이션을 추가하지 마라.** 이유: M5는 스키마를 바꾸지 않는다
- **검증 실패를 무시하고 계속 진행하지 마라** (`|| true`, `set +e`로 감싸기 등). 이유: 이 스크립트가
  곧 검증이며, 통과하는 척하는 데모는 없느니만 못하다
- **"failover", "무중단", "항상 최신", "실시간 동기화" 문구를 쓰지 마라.** 이유: ADR-015·ADR-020이
  금지한 표현이다. 보장 범위는 **버전 일관성과 최신 수렴**, 그리고 **짧은 중단 후 자동 복구**다
- **데모 전용 사용자 데이터를 개발 DB에 남기지 마라.** 이유: `trap`에서 삭제한다
- 기존 테스트를 깨뜨리지 마라
