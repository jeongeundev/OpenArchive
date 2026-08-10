# Step 1: research-failure-injection

## 배경 — "failover를 못 한다"와 "HA 컴포넌트가 놀고 있다"는 다르다

이슈 #27이 실 VM(`opensql-dev`)에 직접 장애를 주입해 측정했다. **후자는 틀렸다는 것이 결론이다.**
그런데 그 측정값이 저장소 어디에도 없다. 지금 문서만 읽으면 Single 구성에서 Patroni·etcd·
OpenProxy가 아무 일도 하지 않는 것처럼 읽힌다.

동시에 **저장소 서술과 실물이 어긋나는 것 세 가지**가 함께 드러났다. 이게 더 급하다.

1. **`archive_mode = on`인데 `archive_command = "/bin/true"`다** — WAL 아카이빙이 켜진 척만 한다
2. **OpenProxy에 Patroni·etcd 연동이 없다** — `use_patroni`도 `[general.etcd]`도 없고
   `servers`에 primary 하나가 하드코딩돼 있다. **ADR-006이 "새 프라이머리 발견·재연결은
   OpenProxy가 수행한다"고 쓰는데 이 설치에는 그 경로가 구성되어 있지 않다**
3. **systemd 유닛은 `opensql-etcd.service` 하나뿐이다** — Patroni·PostgreSQL·OpenProxy는
   `nohup`으로 띄운 맨 프로세스라 죽으면 되살릴 주체가 없다(실측: Patroni kill 후 106초간 무반응).
   Patroni watchdog도 `/dev/watchdog` 권한 부재로 비활성

이 step도 **`docs/OPENSQL_RESEARCH.md` 한 파일만 고친다.** ADR-006·020 정정은 step 2다 —
이 step이 근거를 놓고, step 2가 그것을 인용한다.

## 읽어야 할 파일

- `docs/OPENSQL_RESEARCH.md` — **§0**(step 0이 방금 고친 상태를 확인하고 이어 쓴다),
  **§3 Failover 동작 방식**(202~257행 부근), **§4 OpenProxy의 Failover 기여**(340행 부근),
  **§12 M0 검증 목록**(731행 이하). 이 넷이 수정 대상이다
- `docs/ADR.md` **ADR-020**(731행 부근) — 지금 뭐라고 쓰여 있는지만 확인하라. **고치지는 마라**(step 2)
- `backend/app/api/retry.py` — 앱이 잡는 예외 타입. 아래 실측이 이 코드와 맞는지 확인해야 한다

## 작업

### 1) §0에 새 소절 — 장애 주입 실측

제목: **`### Single 장애 주입 실측 [실측 2026-08-09]`**

**(a) 측정 조건을 먼저 적는다.** 2026-08-09 00:12~00:22 KST, `opensql-dev`(192.168.64.4).
Patroni REST(8008)·etcd v3 HTTP(2379)·OpenProxy(6432)·PG 직결(5432)을 0.5~1초 간격으로 동시에
찌르는 프로버 + VM의 `patroni.log`·`openproxy.log` 대조.

**(b) 정지 상태에서 확인된 클러스터 파라미터**

| 항목 | 값 |
|---|---|
| Patroni | 4.0.5 · scope `opensql` · member `postgresql1` · role `primary` · **timeline 1** |
| 루프 파라미터 | `ttl=30` `loop_wait=10` `retry_timeout=10` **`failsafe_mode=true`** |
| `patronictl history` | **`[]`** — 이 클러스터는 전환을 한 번도 겪은 적이 없다 |
| etcd | 3.6.5 · 단일 멤버 · `leader`·`members/postgresql1`가 하나의 리스에 묶여 **TTL 30초** |

**(c) 시나리오 ① PostgreSQL `SIGKILL` — 감지 46 ms, 접속 재개 5.85초**

| 경과 | 사건 |
|---|---|
| 0 | postmaster `SIGKILL`. 백엔드 전멸 |
| **+46 ms** | Patroni `WARNING: Postgresql is not running.` |
| +233 ms | `INFO: starting primary after failure` |
| **+4.83 s** | `INFO: postmaster pid=...` |
| +5.78 s | crash recovery(redo) **3 ms** |
| **+5.85 s** | `이제 데이터베이스 서버로 접속할 수 있습니다` |

**재기동 지연의 대부분은 감지가 아니라 옛 인스턴스 정리·재연결 확인 구간**(+233 ms → +4.83 s)이다.

**etcd는 이 구간에 아무 일도 겪지 않았다** — `leader` 값은 한 번도 변하지 않았고 TTL 갱신도
끊기지 않았다. **"PostgreSQL이 죽는 것"과 "리더가 바뀌는 것"은 이 제품에서 완전히 분리된 사건이다.**

앱이 받은 예외는 `psycopg.errors.SystemError`이고 MRO가
`SystemError → OperationalError → DatabaseError → Error`라 **`ADR-023`의 재시도가 실제로 이
경로를 탄다.** 5432 직결 쪽은 `OperationalError: connection failed: ... Connection refused`다.

**(d) 시나리오 ② etcd 정지 99초 — `failsafe_mode`가 primary를 지켰다**

| 경과 | 사건 |
|---|---|
| **+15.0 s** | `patroni_failsafe_mode_is_active` 0 → 1 |
| **+27.1 s** | `patroni_cluster_unlocked` 0 → 1 (`ttl=30` 만료와 정합) |
| 전 구간 | `patroni_primary=1`, **6432·5432 모두 쓰기 가능** |
| +99 s → +2.6 s | etcd 재기동 후 `leader` 키 복원, 플래그 전부 0 복귀 |

```
ERROR: Error communicating with DCS
INFO: continue to run as a leader because failsafe mode is enabled and all members are accessible
```

**`failsafe_mode`가 없었다면 Patroni는 `ttl` 만료 시점에 스스로를 강등해 읽기 전용으로 떨어뜨린다**
— 스플릿 브레인 방지가 목적인데, **Single에서는 그 강등이 순수한 손해**라 배포판이 미리 막아 놨다.
**DCS 장애가 곧 서비스 장애는 아니라는 실증이다.**

**(e) 시나리오 ③ Patroni만 `SIGKILL` — PostgreSQL은 멀쩡하고, 되살릴 주체가 없다**

| 경과 | 사건 |
|---|---|
| +0.9 s | Patroni REST(8008) 응답 없음 |
| **+23.9 s** | etcd에서 `leader`·`members/postgresql1` 키 소멸(잔여 TTL과 정합) |
| 전 구간 | **PostgreSQL은 6432·5432 모두 정상, 계속 쓰기 가능** |
| **+106 s** | **아무것도 Patroni를 되살리지 않았다** |

수동 재기동(`start_patroni.sh`) 시 **락 재획득까지 10.1초**이며 **PostgreSQL은 재기동되지 않는다**
— 돌던 postmaster를 그대로 인수하고 `timeline`은 1로 유지된다.

**(f) OpenProxy는 통보받지 않는다 — 실패해야 안다**

`openproxy.log` 실측에서 읽히는 것 셋:

1. **백엔드 축출은 요청이 실패한 순간에 일어난다**(축출까지 **278 ms**). 헬스체크 타이머가 아니라
   에러가 방아쇠다
2. **`pool_mode = "session"`이라 클라이언트 연결도 같이 끊긴다** — 앱이 `OperationalError`를
   보는 이유가 이것이다
3. **재연결도 클라이언트 요청에 이끌려 일어난다**

> ⚠️ 로그의 재연결 성공 시각을 "OpenProxy의 복구 지연"으로 읽으면 안 된다. PG가 수락을 시작한
> 뒤 요청이 없던 공백이 섞여 있다. **OpenProxy의 복구 지연은 별도 값이 아니다.**

### 2) §0에 새 소절 — 설치 실태가 서술과 어긋나는 세 가지

제목: **`### 설치 실태 — 저장소 서술과 어긋나는 것 [실측 2026-08-09]`**

배경에 적은 3건을 각각 **무엇이 어긋났고 무엇에 영향을 주는지**까지 쓴다.

- `archive_command = "/bin/true"` → **DR(백업·PITR)이 실질적으로 꺼져 있다.**
  barman을 "채택 비용 0"으로 본 판정의 전제가 흔들린다(Patroni 관리 설정 변경이 선행 조건).
  다만 **#29가 barman을 기각했으므로 지금 고쳐야 할 대상은 아니다** — 사실만 기록한다
- OpenProxy에 Patroni·etcd 연동 없음 → **ADR-006 정정 대상**임을 명시한다.
  노드가 하나라 모순은 아니지만 서술과 실물이 어긋난다
- systemd 유닛 1개 + watchdog 비활성 → **"HA 구성이 완전히 살아 있다"고 말하면 틀린다**

배포판 `$OPENSQL_HOME/scripts/`에 **`finalize_single_to_ha.sh`**가 있다는 사실도 함께 적되,
**그것이 사무국의 Single 지시를 어길 근거가 되지 않는다**는 단서를 반드시 붙인다.

### 3) §4 "OpenProxy의 Failover 기여" 절에 실측 정정을 단다

이 절은 공식 문서 기준 서술이다. **삭제하지 말고** 그 아래에 정정을 붙인다 — 이 설치의
OpenProxy는 **정적 서버 목록을 가진 순수 커넥션 풀러**이며 프라이머리 발견 경로가 구성되어
있지 않다. 축출·재연결이 **요청 구동**이라는 실측도 함께 적는다.

### 4) §12 검증 목록을 갱신한다

- **`### ✅ #27 장애 주입 측정 완료 (2026-08-09)`** 항목을 추가하고 위 세 시나리오를 한 줄씩 요약
- **`### ⛔ Single 구성에서 검증 불가능한 항목`** 표를 다시 읽고 **정확히 네 가지로 정리**한다:
  ① 리더 선출·승격·`timeline` 증가(승격 대상 replica 없음, `patronictl history`가 `[]`)
  ② OpenProxy의 새 프라이머리 자동 발견(**기능 이전에 설정 자체가 없다**)
  ③ watchdog 펜싱(`/dev/watchdog` 권한 없음)
  ④ VIP failover
- **`### 🔴 아직 남은 실측`**에서 이번에 해소된 항목이 있으면 표시한다

### 5) §0 "실 VM 실측 결과"의 Failover 행을 정확하게 고친다

지금은 이렇게만 적혀 있다:

| Failover | ⛔ Single 구성이라 **원리적으로 불가** (ADR-020) |

**틀리지는 않았으나 좁다.** "failover 불가"와 "자동 복구 없음"이 다르다는 것이 이번 측정의
핵심이므로, **PG 프로세스 장애 자동 복구는 실측됐다**는 사실과 새 소절 참조를 덧붙인다.

## Acceptance Criteria

```bash
# 1) 세 시나리오의 실측값이 들어갔는지 — 숫자로 확인한다
grep -n "46 ms\|46ms" docs/OPENSQL_RESEARCH.md
grep -n "5.85" docs/OPENSQL_RESEARCH.md
grep -n "failsafe_mode" docs/OPENSQL_RESEARCH.md
grep -n "23.9" docs/OPENSQL_RESEARCH.md

# 2) 설치 실태 3건이 기록됐는지
grep -n "/bin/true" docs/OPENSQL_RESEARCH.md
grep -n "use_patroni" docs/OPENSQL_RESEARCH.md
grep -n "opensql-etcd.service" docs/OPENSQL_RESEARCH.md
grep -n "watchdog" docs/OPENSQL_RESEARCH.md

# 3) 검증 불가 항목이 네 가지로 정리됐는지 (표를 눈으로 확인할 것)
sed -n '/Single 구성에서 검증 불가능한 항목/,/^## /p' docs/OPENSQL_RESEARCH.md

# 4) 앱이 잡는 예외 서술이 실제 코드와 맞는지
grep -n "OperationalError" backend/app/api/retry.py

# 5) 이 step도 조사 문서 한 개만 고친다
git diff --name-only | grep -vE "^(docs/OPENSQL_RESEARCH\.md|phases/)"

# 6) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **step 0이 쓴 소절을 덮어쓰거나 지우지 않았는가?** 두 step이 같은 §0을 고친다
   - `psycopg.errors.SystemError` → `OperationalError` 서브클래스 서술이
     `backend/app/api/retry.py`의 실제 `except` 절과 맞는가? **틀리면 문서가 아니라 여기서 멈춰라**
   - "failover 불가"와 "PG 프로세스 장애 자동 복구는 된다"가 **한 문서 안에서 모순 없이** 읽히는가?
   - `finalize_single_to_ha.sh` 언급에 "지시를 어길 근거가 아니다"라는 단서가 붙었는가?
3. 결과에 따라 `phases/m6-docs-recovery/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **VM에 접속해 장애를 다시 주입하지 마라.** 이유: 이 step은 **이미 측정된 값을 기록**하는
  작업이다. 재측정은 라이선스 만료 전 스냅샷 관리(ADR-021)의 일이고 여기서 결정할 일이 아니다
- **`docs/ADR.md`를 수정하지 마라.** 이유: ADR-006·020 개정은 step 2다. 이 step은 그 근거만 놓는다
- **`archive_command`를 고치자고 제안하거나 스크립트를 만들지 마라.** 이유: #29가 barman을
  기각했다. 사실 기록이 전부다
- **코드 파일(`.py`, `.ts`, `.tsx`, `.sql`, `.sh`)을 수정하지 마라.**
- **"무중단"·"failover를 시연했다"를 쓰지 마라.** 이유: 하지 않았다. 정확한 표현은
  **"DB 프로세스 장애 자동 복구"**이며 이 문구는 step 2가 ADR-020에 고정한다
