# Step 6: demo-recovery-rewrite

## 배경 — 주연을 앱에서 OpenSQL로 바꾼다

현재 `scripts/demo_recovery.sh` 264줄은 `DB_STOP_CMD="docker compose stop db"`로
**로컬 컨테이너를 통째로 정지**한다. 즉 **OpenSQL 컴포넌트가 하나도 개입하지 않는 경로**이며,
증명하는 것이 전부 애플리케이션 계층이다. 스크립트 마지막 줄이 스스로 밝히고 있다 —
*"이 데모는 Patroni 리더 선출·승격과 그 소요 시간을 증명하지 않습니다."*

더 나쁜 것은 **자동 복구가 아니라는 점**이다. `docker compose stop`은 사람이
`docker compose start`를 줘야 복구된다. **실 OpenSQL에서는 Patroni가 스스로 되살린다** —
그것이 이 제품을 쓰는 이유이고, 데모가 보여주지 못하던 것이다.

#30의 결정:

- 시나리오는 **① 실 OpenSQL에서 PG `kill -9`** 하나다. ②etcd ③Patroni는 **코드 0줄, 문서로만**
- **사람 개입 1회** → Patroni 자동 재기동 → 앱 재연결 → 잡 재개 → 정합성 0이 **한 타임라인**에 찍힌다
- **도커 경로를 폐기한다.** `check.sh`·CI 어디에도 없어 깨질 것이 없다
- **심사위원은 라이선스가 없어 이 스크립트를 못 돌린다** — "돌려볼 수 있음"은 값어치가 아니다.
  통로는 **영상과 결과보고서**이므로, **출력이 곧 대본이 되도록** 만든다

## ⚠️ 선행 조건 — 실 OpenSQL VM이 필요하다

이 step은 **실제로 DB를 죽인다.** 시작 전에 확인하라:

```bash
nc -z -w 3 "$OPENSQL_HOST" 6432 && nc -z -w 3 "$OPENSQL_HOST" 8008
ssh -o ConnectTimeout=4 "$OPENSQL_HOST" 'hostname; sudo -n true && echo NOPASSWD_OK'
```

**하나라도 실패하면 `"status": "blocked"`, `"blocked_reason"`에 실패한 항목을 적고 즉시 중단하라.**
추측으로 통과 처리하지 마라. 기본값은 `OPENSQL_HOST=192.168.64.4`다.

## 읽어야 할 파일

- `scripts/demo_recovery.sh` — 재작성 대상. **버릴 것과 살릴 것을 구분해야 하므로 전체를 읽어라.**
  헬퍼(`wait_until`·`status_value`·`db_value`·`jobs_drained`·`consistent`·`upload_document`·
  `edit_document`·`cleanup`)는 **대부분 그대로 쓸 수 있다**
- `docs/ADR.md` **ADR-020** — step 2가 고정한 표현. **데모 출력이 여기와 같은 말을 해야 한다**
- `docs/OPENSQL_RESEARCH.md` §0 「Single 장애 주입 실측」 — step 1이 기록한 사건 순서.
  데모가 관측할 대상이 그대로 여기 있다
- `docs/SETUP_OPENSQL.md` — VM 접속 정보와 OpenProxy DSN 형식(**dbname 자리가 pool 이름이다**)
- `backend/app/api/system.py` — `/api/system/status` 응답 필드

## 작업

### 1) `scripts/demo_recovery.sh`를 재작성한다

**환경변수 (기본값)**

| 변수 | 기본값 | 용도 |
|---|---|---|
| `OPENSQL_HOST` | `192.168.64.4` | VM 주소 |
| `OPENSQL_SSH` | `$OPENSQL_HOST` | ssh 접속 대상 |
| `DATABASE_URL` | `postgresql://postgres:pg_password@$OPENSQL_HOST:6432/opensql` | **OpenProxy 경유**. dbname 자리는 pool 이름이다 |
| `PATRONI_URL` | `http://$OPENSQL_HOST:8008` | 클러스터 상태 |
| `API_PORT` | `18000` | 데모용 API |

**`DB_STOP_CMD`·`DB_START_CMD`를 삭제한다.** 정지 명령이 없다 — 죽이는 것은 `kill -9` 하나이고
되살리는 것은 Patroni다.

**흐름**

```
0. 사전 점검
   - 6432 접속 / Patroni 8008 응답 / ssh + sudo -n
   - 스키마 존재 (documents 테이블)
   - backend/.venv 존재
   실패 시 무엇이 없어서 못 하는지 정확히 출력하고 종료

1. 기준선
   - patronictl 상태: role · timeline(TL) 기록  ← 마지막에 다시 읽어 비교한다
   - jobs 카운트 · inconsistent_documents = 0 확인

2. 파이프라인 가동
   - API·워커 기동 (OpenProxy 경유 DSN)
   - 문서 업로드 → 임베딩 완료 대기 → 정합성 0 확인
   - 문서를 편집해 pending 잡을 만든다  ← 장애 순간에 처리 중인 일이 있어야 한다

3. ★ 사람 개입 1회 — 여기가 유일한 수동 조작이다
   ssh "$OPENSQL_SSH" 'sudo kill -9 <postmaster pid>'
   postmaster PID는 원격에서 확인해 얻는다 (자식 백엔드가 아니라 부모여야 한다)

4. 타임라인 관측 — 각 사건의 경과를 초 단위로 찍는다
   t0  kill 시각
   t1  앱이 받은 첫 예외 (OperationalError 계열, 클래스명을 그대로 출력)
   t2  Patroni 로그의 "Postgresql is not running"
   t3  Patroni 로그의 "starting primary after failure"
   t4  6432 재접속 성공
   t5  pending 잡 재개 완료 (0으로 수렴)
   t6  inconsistent_documents = 0

5. 승격이 없었음을 확인한다
   - TL이 기준선과 같은지 비교한다. 달라졌으면 실패로 처리하라 —
     Single에서 TL이 오르면 우리가 이해하지 못한 일이 일어난 것이다

6. 요약 출력 — 한 타임라인 한 화면
7. 한계 명시 출력 (아래 2번)
```

**Patroni 로그 관측**은 ssh로 원격 로그를 읽는다. 로그 경로를 하드코딩하지 말고 변수로 두되,
**찾지 못하면 그 사실을 출력하고 계속 진행하라** — 로그는 서사를 풍부하게 하지만 없어도
t1·t4·t5·t6으로 데모는 성립한다. **로그 부재로 데모를 실패시키지 마라.**

### 2) 출력 문구를 ADR-020에 고정된 표현과 일치시킨다

마지막에 반드시 출력할 것:

```
완료: DB 프로세스 장애로부터의 자동 복구를 확인했습니다.
      Patroni가 감지하고 스스로 재기동했으며, 사람의 개입은 kill 1회뿐입니다.

한계: 노드 사망은 복구되지 않습니다. 노드 2대 이상이 물리적 전제이며,
      사무국 지시에 따른 Single 구성의 제약입니다.
      리더 선출·승격은 일어나지 않았습니다 (timeline 유지: TL <n>).
```

**금지 표현이 들어가지 않았는지 스스로 확인하는 문구를 쓰지 마라** — 스크립트가 자기를 검사할
필요는 없다. AC가 검사한다.

### 3) 도커 경로를 완전히 없앤다

`docker compose`를 부르는 줄, `DB_STOP_CMD`/`DB_START_CMD`, 로컬 5433 DSN 기본값을 전부
제거한다. **로컬 컨테이너로 이 데모를 돌리는 경로는 남기지 않는다** — 남기면 "어느 쪽으로
돌렸는지" 모르는 결과가 나온다.

### 4) 실패 시 진단이 되게 한다

`fail()`은 지금처럼 API·워커 로그 경로를 출력한다. **여기에 더해** 마지막으로 성공한 단계와
그 시각을 출력하라. 영상 촬영 중 실패하면 어디서 멈췄는지가 바로 필요하다.

### 5) 정리(cleanup)는 반드시 남긴다

데모가 만든 문서를 지우고 API·워커를 종료한다. **DB는 건드리지 않는다** — Patroni가 되살린
인스턴스를 다시 손대면 다음 실행의 기준선이 흔들린다.

## Acceptance Criteria

```bash
# 1) 실행 가능하고 구문이 맞는지
test -x scripts/demo_recovery.sh
bash -n scripts/demo_recovery.sh

# 2) 도커 경로가 사라졌는지 — 출력이 없어야 한다
grep -nE "docker compose|DB_STOP_CMD|DB_START_CMD|5433" scripts/demo_recovery.sh

# 3) 실 OpenSQL 경로가 들어갔는지
grep -n "6432" scripts/demo_recovery.sh
grep -n "8008" scripts/demo_recovery.sh
grep -nE "kill -9|SIGKILL" scripts/demo_recovery.sh

# 4) 금지 표현이 없는지 — 출력이 없어야 한다
grep -nE "무중단|항상 최신|실시간 동기화|failover를 시연" scripts/demo_recovery.sh

# 5) ADR-020 표현이 출력에 있는지
grep -n "DB 프로세스 장애" scripts/demo_recovery.sh
grep -n "노드 사망" scripts/demo_recovery.sh

# 6) ★ 실제로 돌린다 — 이것이 이 step의 진짜 AC다
bash scripts/demo_recovery.sh

# 7) 사전 점검이 실제로 막는지 — 닿지 않는 호스트로 돌리면 즉시 실패해야 한다
OPENSQL_HOST=203.0.113.1 bash scripts/demo_recovery.sh; echo "exit=$?"

# 8) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **6번을 실행하지 못했다면 통과 처리하지 말고 `blocked`로 두어라.**
2. 아키텍처 체크리스트를 확인한다:
   - **사람 개입이 정말로 1회인가?** `kill -9` 말고 다른 수동 조작이 있으면 "자동 복구"가 아니다
   - **한 타임라인에 t0~t6가 전부 찍혔는가?** 사건이 흩어지면 영상에서 서사가 안 된다
   - **TL이 기준선과 같은가?** 달라졌으면 통과시키지 말고 조사하라
   - 출력 문구가 **step 2가 ADR-020에 고정한 표현과 글자 그대로 같은가?**
   - **DB에 남긴 흔적이 없는가?** 데모 문서가 정리됐고 스키마가 그대로인가
   - 실패 경로에서 **마지막 성공 단계**가 출력되는가
3. 결과에 따라 `phases/m6-docs-recovery/index.json`의 step 6을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - VM 접속 불가 등 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **VM의 설정 파일을 고치지 마라.** `patroni.yml`·`openproxy.toml`·`postgresql.conf` 전부.
  이유: 데모는 **있는 그대로의 설치**에서 일어나야 한다. 손대면 그 결과가 배포판의 동작이 아니다
- **`archive_command`를 고치지 마라.** 이유: #29가 barman을 기각했고 DR은 이 데모의 범위가 아니다
- **etcd·Patroni를 죽이는 시나리오를 코드로 넣지 마라.** 이유: #30이 **코드 0줄, 문서로만**으로
  정했다. 시나리오가 셋이 되면 영상 30초 안에 안 들어간다
- **`/admin/status` 화면이나 API에 Patroni 상태를 추가하지 마라.** 이유: #30이 기각했다 —
  플랫폼을 쓰는 것은 유저이고 유저가 `timeline`을 볼 이유가 없다
- **로컬 컨테이너로도 돌아가는 "겸용" 경로를 만들지 마라.** 이유: 도커 경로 폐기가 결정이고,
  겸용은 결과의 출처를 흐린다
- **`check.sh`에 이 데모를 넣지 마라.** 이유: `check.sh`는 매 응답마다 도는 훅이다.
  DB를 죽이는 스크립트를 거기 넣으면 안 된다
- **테스트를 약화하거나 건너뛰지 마라.** 데모가 실패하면 스크립트를 고쳐라. 단언을 지우지 마라
