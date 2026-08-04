# OpenSQL 아키텍처 조사 결과

> 조사일: 2026-08-04
> 출처: Tmax OpenSQL 3.0 공식 매뉴얼 v1.5.0 (`docs.tibero.com/tmaxopensql`), Tmax OpenSQL GitHub 조직
> 목적: `PROJECT_CONTEXT.md` 설계 원칙("OpenSQL 공식 아키텍처를 우선한다", "OpenSQL 기능을 애플리케이션에서 중복 구현하지 않는다")에 따라 조사 결과로 ADR·Architecture를 확정하기 위함

## 신뢰도 표기

| 표기 | 의미 |
|---|---|
| **[확정]** | 공식 문서 본문에서 직접 확인 (설정 예제, 표, 버전 출력 등) |
| **[요약]** | 문서 사이트의 질의응답 기능을 통해 얻은 요약. 원문 그대로가 아닐 수 있음 |
| **[미확인]** | 공식 문서에 언급 자체가 없음. 실 클러스터에서 검증 필요 |

---

## 1. 구성요소

**[확정]** OpenSQL v3.0은 단일 DBMS가 아니라 **4개 컴포넌트로 구성된 클러스터 제품**이다.

| 컴포넌트 | 기반 기술 | 버전 | 역할 |
|---|---|---|---|
| **OpenSQL Database** | PostgreSQL | **16.8 또는 14.13** ⚠️ | 데이터 노드 |
| **OpenHA Cluster Manager** | Patroni | patronictl **4.0.5** | 노드 상태 감시, 자동 Failover, Primary 선출 |
| **OpenHA DCS** | etcd | **3.5.6 / 3.5.21** | 클러스터 멤버십·구성 정보 분산 저장 |
| **OpenProxy** | Rust 자체 구현 | **1.1.0 ~ 1.1.3** | 커넥션 풀링, 로드밸런싱, 읽기/쓰기 분리, VRRP VIP Failover |
| Barman | Python | — | 백업/복구 (전체·증분·차등) |

> ⚠️ **PostgreSQL 메이저 버전이 하나로 고정되어 있지 않다.** 문서에 **16.8과 14.13이 함께** 언급된다. PG14와 PG16은 사용 가능한 pgvector 버전과 일부 SQL 기능이 다르므로, **제공받는 클러스터의 실제 메이저 버전 확인이 M0 필수 항목**이다.

### 번들 익스텐션 목록 **[확정]**

통합 설치 시 함께 설치되는 구성요소 (라이선스 에디션에 따라 결정):

**Core**: `postgresql`, `etcd`, `patroni`, `openproxy`

**Extensions**:
`postgis`, `pg_hint_plan`, `pg_cron`, **`pgvector`**, **`pgvectorscale`**, `credcheck`, `system_stats`, `pgaudit`, `opencrypto`, `o2`, `pg_profile`, `tibero_fdw`

> **설계 영향 (중요)**
> - `pgvector`뿐 아니라 **`pgvectorscale`도 번들**이다. pgvectorscale은 StreamingDiskANN 인덱스를 제공하며 HNSW와 다른 특성을 가진다. `ADR-002`(HNSW 선택)는 이 선택지를 모르고 내린 결정이므로 재검토 대상.
> - **`pg_cron`이 번들**이다. `ADR-001`은 "pg_cron 폴링은 쓰지 않는다"고 했는데, 이는 여전히 유효한 결정일 수 있으나 "쓸 수 없어서"가 아니라 "쓸 수 있지만 안 쓴다"로 근거를 다시 써야 한다.

### 설치 방식 **[확정]**
- Python 기반 설치기: `opensql_local_installer.py`(노드별) / `opensql_remote_installer.py`(SSH 중앙 배포)
- 바이너리 개별 설치도 가능: `create_user.sh` → 환경변수(`OPENSQL_HOME`, `PG_HOME`, `PG_DATA_DIR`) → `setenv.sh` → `install.sh postgresql`
- 패키지 예시: `Tmax_OpenSQL_3.18.1.3`

---

## 2. 권장 아키텍처 **[확정]**

### 3노드 구성 (공식 권장)

> "3-Node 구조는 전형적인 고가용성 구성으로, 노드 간 이중화와 자동 Failover 기능을 제공한다"

| 노드 | 구성요소 | 예시 IP |
|---|---|---|
| Node 1 | PostgreSQL, OpenHA | 178.176.0.2 |
| Node 2 | PostgreSQL, OpenHA, **OpenProxy** | 178.176.0.3 |
| Node 3 | PostgreSQL, OpenHA, **OpenProxy** | 178.176.0.4 |

- **OpenProxy는 2개 인스턴스로 이중화**하고 그 위에 VRRP VIP를 띄우는 것이 표준 형태
- 2노드 구성은 정족수 유지를 위해 **witness 노드**가 추가로 필요 (최소 2 vCPU / 8GB / 50GB SSD)

### 시스템 요구사항 **[확정]**
- x86-64
- OS: Oracle Linux 8/9, Rocky Linux 8/9, RHEL 8/9, Ubuntu 22.04 / 24.04
- 설치 시 sudo 권한 필요
- Rust 설치 경로 `$OPENSQL_RUST_BASE` (기본 `/opt/opensql/rust`)
- **HA 환경은 NTP/Chrony 시간 동기화가 필수** (기동 전 선행)

---

## 3. Failover 동작 방식 (Patroni) **[확정]**

### OpenSQL이 실제로 배포하는 `patroni.yml` **[확정]**

파일 위치: `$OPENSQL_HOME/etc/patroni.yml` (기본 템플릿)

`bootstrap.dcs`:

| 파라미터 | 값 | 의미 |
|---|---|---|
| `ttl` | 30초 | 리더 임대 만료 시간 |
| `loop_wait` | 10초 | Failover 검사 주기 |
| `retry_timeout` | 10초 | 연결 재시도 윈도우 |
| `maximum_lag_on_failover` | 1,048,576 바이트 (1MB) | 승격 후보의 최대 허용 복제 지연 |
| **`failsafe_mode`** | **`true`** | DCS 접근 불가 시에도 Primary를 강등하지 않음 (스플릿브레인 방지 완화) |

PostgreSQL 파라미터:

| 파라미터 | 값 |
|---|---|
| **`max_connections`** | **100** |
| `max_worker_processes` | 8 |
| `log_line_prefix` | `'%m [%r] [%u] [%a]'` |

REST API: `listen: 0.0.0.0:8008`
복제 계정: `patroni_repl`, `patroni_rewind`, 슈퍼유저 `postgres`

> **이 값들은 Patroni 일반 기본값이 아니라 OpenSQL이 지정한 템플릿 값이다.** 따라서 리더 장애 감지부터 새 Primary 승격까지 **수십 초 단위**가 걸린다는 추론은 문서 확인된 수치에 기반한다. **"무중단"이 아니라 "짧은 중단 후 자동 복구"**가 정확한 표현이다.
>
> **`max_connections = 100`은 설계 제약이다.** API 커넥션 풀 + 워커 잡 처리 풀 + 워커 LISTEN 전용 연결이 모두 이 안에 들어가야 한다. OpenProxy가 앞단에서 풀링해 완화해주지만, `pool_size` 설정과 함께 계산해야 한다.

### 승격 규칙
- 복제 가능한 Replica 중에서 선출
- 복제 지연이 `maximum_lag_on_failover` 이내인 노드만 후보
- `failover_priority`, `nofailover` 태그로 우선순위 조정 가능

### 운영 명령 (`patronictl -c <path>/patroni.yml <command>`)

| 명령 | 용도 |
|---|---|
| `patronictl list` | 멤버·역할·상태·복제 지연 조회 |
| `patronictl failover` | 리더 부재 시 수동 Failover |
| `patronictl switchover` | 계획된 리더 전환 (유지보수용) |
| `patronictl pause` / `resume` | 자동 Failover 비활성/활성 |
| `patronictl history` | Failover/Switchover 감사 로그 |
| `patronictl dsn [-r role]` | 특정 역할의 접속 문자열 반환 |

> **데모 관점**: `patronictl switchover`로 계획된 전환을, `patronictl failover`(또는 프로세스 kill)로 장애를 재현할 수 있다. `patronictl history`가 그대로 **"장애가 실제로 있었다"는 증거**가 된다.

### 클러스터 상태 노출
- **etcd**: `/<namespace>/<scope>/` 경로에 멤버·역할·복제 슬롯·동적 설정 저장
- **REST API**: 기본 `0.0.0.0:8008`
- 애플리케이션/커넥션 풀러는 etcd 조회 또는 REST API 감시로 현재 Primary를 발견

---

## 4. 공식 접속 방식 (Connection) **[확정 / 일부 요약]**

### 클라이언트 → OpenProxy

**[요약]** 클라이언트는 OpenProxy를 PostgreSQL 서버처럼 취급해 접속한다.

- 포트: `[general] port` (기본 **5432**, PgBouncer 관례를 따라 **6432** 통용)
- 접속 시 지정하는 **database 이름 자리에 pool 이름**을 넣는다
- 인증: `auth_type` = `md5` | `scram-sha-256`
- 예시: `psql -h <vip> -p 6432 -U <user> <pool_name>`

### OpenProxy → 백엔드 노드 **[확정]**

멀티호스트 라우팅 로직은 **OpenProxy 설정 파일 안에** 선언한다.

```toml
[pools.simple_db]
pool_mode = "session"                        # 또는 "transaction"
query_parser_enabled = true
query_parser_read_write_splitting = true
primary_reads_enabled = true
sharding_function = "pg_bigint_hash"

[pools.simple_db.users.0]
username = "simple_user"
password = "simple_user"
pool_size = 5
statement_timeout = 30000

[pools.simple_db.shards.0]
servers = [
  [ "opensql1", 5432, "Auto", ],
  [ "opensql2", 5432, "Auto", ],
]
database = "some_db"
use_patroni = true
```

- 역할(role): `"primary"` | `"replica"` | `"Auto"`
- `use_patroni = true` + `[general.etcd]` 설정 시 **역할을 Patroni가 관리**하고 OpenProxy가 etcd를 통해 추적
- `load_balancing_mode`: `"random"` | `"loc"`(Least Open Connections)

### `[general]` 섹션 기본값 **[확정]**

| 파라미터 | 기본값 | 비고 |
|---|---|---|
| `host` | `0.0.0.0` | |
| `port` | `6432` | ⚠️ 다른 문서 페이지에서는 기본 5432로 서술 — **상충. 실측 필요** |
| `worker_threads` | 5 | |
| `connect_timeout` | 1000 ms | |
| `idle_timeout` | 600000 ms (10분) | |
| `server_lifetime` | 3600000 ms (1시간) | 서버 커넥션 최대 수명 |
| `healthcheck_timeout` | 1000 ms | |
| `healthcheck_delay` | 30000 ms (30초) | 백엔드 헬스체크 주기 |
| `shutdown_timeout` | 60000 ms | 진행 중 트랜잭션 완료 대기 |
| `ban_time` | 60초 | 장애 노드 격리 시간 |
| `auth_type` | `md5` | `scram-sha-256`도 지원 |
| `server_tls` | `false` | |

VRRP 설정 블록:
```toml
[general.virtual_router]
interface = "eth0"
router_id = 50           # 1-255
priority = 50            # 0-255
advert_int = 3
vip_addresses = ["178.176.0.200/24"]
```

### 운영 스크립트 **[확정]**

```bash
bash $OPENSQL_HOME/scripts/start_openproxy.sh    # 설정: $OPENSQL_HOME/etc/openproxy.toml
bash $OPENSQL_HOME/scripts/stop_openproxy.sh     # SIGTERM
bash $OPENSQL_HOME/scripts/restart_openproxy.sh
bash $OPENSQL_HOME/scripts/reload_openproxy.sh   # SIGHUP, 무중단 설정 반영
```

> `server_lifetime`(1시간)과 `idle_timeout`(10분)은 **워커의 장수 LISTEN 연결에 직접 영향을 준다.** OpenProxy를 경유하면 LISTEN 연결이 최대 1시간마다 강제로 끊길 수 있다 (§7과 연결).

### OpenProxy의 Failover 기여 **[확정]**

공식 기능 목록에 다음이 명시되어 있다:

- 커넥션 풀링
- **읽기/쓰기 분리** (쿼리 자동 분석 → 쓰기는 Primary, 읽기는 Replica)
- 로드밸런싱 (읽기 쿼리를 여러 Replica에 분산)
- **자동 Failover — Primary 변경을 감지하고 새 Primary로 재연결**
- etcd 연동 (여러 OpenProxy 인스턴스 간 설정 공유)
- **VRRP/VIP 지원** (인스턴스 간 고가용성)

> **설계 영향 (치명적)**
> "새 Primary 자동 발견 후 재연결"은 **OpenProxy가 이미 제공하는 기능**이다. 애플리케이션이 멀티호스트 DSN + `target_session_attrs=read-write`로 이를 직접 구현하는 것은 `PROJECT_CONTEXT.md`의 설계 원칙 **"OpenSQL 기능을 애플리케이션에서 중복 구현하지 않는다"**를 정면으로 위반한다. → `ADR-006` 폐기 및 재작성 필요.

---

## 5. Pool Mode와 세션 상태 제약 **[확정]**

**`pool_mode` 기본값은 `transaction`이다.** **[확정]**

| 모드 | 동작 | 제약 |
|---|---|---|
| **`transaction`** (**기본값**, 문서상 권장) | 트랜잭션 시작 시 서버 커넥션 할당, 종료 즉시 반납. 여러 클라이언트가 순차 재사용 | 트랜잭션 **밖**에서 쓰는 `SET`·임시 테이블은 의도치 않게 동작. `PREPARE`/`EXECUTE`는 "트랜잭션이 서로 다른 서버 커넥션에서 실행될 수 있어 정상 동작하지 않습니다" |
| **`session`** | 클라이언트 접속 동안 서버 커넥션 전용 유지 | 커넥션 절감 효과 감소. 세션 상태(`SET`, prepared statement, 임시 테이블)를 쓰는 앱에 적합 |

> **설계 영향**: 기본값이 `transaction`이라는 사실이 §7의 LISTEN/NOTIFY 리스크를 **강화**한다. 아무 설정도 하지 않으면 세션 상태가 유지되지 않는 모드로 동작하기 때문이다.

**[확정]** `query_parser`(읽기/쓰기 분리) 사용 시 `transaction` 모드 한정 제약:
1. `PREPARE` / `EXECUTE` — 정상 동작하지 않음
2. DDL/DML을 포함한 `CREATE FUNCTION` — `SELECT` 기반 호출이 Replica로 라우팅되어 오류 발생. 우회: `BEGIN … COMMIT`으로 감싸 Primary 강제

**[확정]** `session` 모드 고유 제약은 문서에 없음.

---

## 6. 읽기/쓰기 분리와 복제 지연 **[확정]** ⚠️ 이 프로젝트에 결정적

### 라우팅 규칙
- **명시적 트랜잭션 블록 밖의 단순 `SELECT`** (Simple/Extended Query Protocol 모두) → **Replica로 라우팅**
- `BEGIN … COMMIT` 안의 문장 → **Primary로 라우팅**
- `primary_reads_enabled = true`면 Primary도 읽기 대상에 포함

### 문서화된 한계
> **복제 지연 인식(replication-lag awareness) 없음. read-your-writes 보장 없음.**
> Primary 강제 라우팅 수단은 명시적 트랜잭션 블록으로 감싸는 것 외에 문서에 없음.

### 기본값 **[확정]** — 2026-08-04 정정

| 파라미터 | 기본값 |
|---|---|
| `query_parser_enabled` | **`false`** |
| `query_parser_read_write_splitting` | **`false`** |
| `primary_reads_enabled` | **`false`** |

**읽기/쓰기 분리는 기본적으로 꺼져 있다.** 즉 아무 설정도 하지 않으면 모든 쿼리가 Primary로 간다.

> **설계 영향 (조건부 — 설정에 종속)**
>
> 기본값이 `false`이므로 "가만두면 깨진다"는 아니다. **그러나 방심할 수 없는 이유가 있다:**
>
> **공식 설치 가이드의 예제 `openproxy.toml`이 이 옵션들을 켜 놓고 있다.**
> ```toml
> [pools.postgres]
> pool_mode = "transaction"
> query_parser_enabled = true              # ← 켜져 있음
> query_parser_read_write_splitting = true # ← 켜져 있음
> ```
> 기업이 제공하는 클러스터가 이 예제를 따라 구성돼 있다면 읽기/쓰기 분리가 **활성 상태**로 온다.
>
> 활성 상태일 때 무슨 일이 벌어지는가:
> 이 플랫폼의 검색 쿼리는 트랜잭션 밖 단순 `SELECT`다 → **Replica로 라우팅된다.**
> 워커가 Primary에 청크를 커밋한 직후 사용자가 검색하면, 복제 지연 때문에 **방금 임베딩된 청크가 검색 결과에 없을 수 있다.**
> 기업이 제시한 핵심 문제인 **"원본 데이터와 벡터 데이터의 정합성"**과 정면으로 충돌한다. 트랜잭션 아웃박스로 DB 안에서 정합성을 보장해놓고, 읽기 경로에서 그 보장이 깨지는 구조다.
>
> 대응 후보:
> 1. **제공받은 클러스터의 실제 설정을 `SHOW CONFIG`로 먼저 확인** (M0)
> 2. 검색 쿼리를 명시적 트랜잭션으로 감싸 Primary 강제 (가장 단순·확실. 설정과 무관하게 안전)
> 3. 검색용 pool을 `query_parser_read_write_splitting = false`로 별도 구성
>
> 어느 쪽이든 **의식적 결정과 ADR 기록이 필요**하다. "기본값이 off라서 괜찮다"고 넘기면, 운영 환경 설정이 다를 때 조용히 깨진다.

---

## 7. LISTEN / NOTIFY **[미확인]** ⚠️ 최대 리스크

**공식 문서 전체에 `LISTEN`/`NOTIFY`에 대한 언급이 단 한 줄도 없다.** 지원한다고도, 안 한다고도 쓰여 있지 않다.

- DB 계층(PostgreSQL 16)에서는 당연히 표준 동작한다
- 문제는 **OpenProxy를 경유할 때**다
- `LISTEN`은 세션 상태이므로, 이론적으로 `transaction` 모드에서는 동작하지 않을 가능성이 매우 높다 (PgBouncer 계열의 알려진 제약과 동일 구조)
- `session` 모드에서는 동작할 가능성이 있으나 **문서로 확인되지 않음**

> **설계 영향 (치명적)**
> 현재 `ARCHITECTURE.md`는 `pg_notify` → 워커 `LISTEN`을 **주 경로**로, 10초 폴링을 **안전망**으로 설계했다. 이 주 경로가 검증되지 않은 가정 위에 서 있다.
>
> **M0 최우선 검증 항목.** 결과에 따라 두 갈래:
> - 동작함 → 워커 LISTEN 연결만 `session` 모드 전용 pool 또는 노드 직결로 분리
> - 동작 안 함 → 폴링을 주 경로로 승격하고 NOTIFY를 최적화로 격하 (설계 서사 수정)

---

## 8. pgvector / HNSW **[일부 확정, 일부 미확인]**

| 항목 | 상태 |
|---|---|
| pgvector 번들 여부 | **[확정]** 번들됨 |
| pgvectorscale 번들 여부 | **[확정]** 번들됨 (StreamingDiskANN 제공) |
| pgvector **버전** | **[미확인 — 확정적]** 개요·설치·릴리즈 노트 전부 확인했으나 **어디에도 명시 없음** |
| **HNSW 인덱스 지원 여부** | **[미확인]** 문서에 언급 없음 |
| `hnsw.iterative_scan` (pgvector 0.8+) | **[미확인]** 버전 미확인이므로 판단 불가 |

> **문서로는 더 얻을 수 없다.** 개요, 통합 설치 페이지, 릴리즈 노트 1.0.0~1.4.0을 모두 확인했으나 pgvector·pgvectorscale의 버전 번호가 문서 어디에도 없다. **실 클러스터 접속 후 쿼리로 확인하는 것이 유일한 방법이다.**
> ```sql
> SELECT extname, extversion FROM pg_extension;
> SELECT * FROM pg_available_extensions WHERE name LIKE '%vector%';
> ```

> **설계 영향**
> - `ARCHITECTURE.md:125`의 "pgvector 0.8+ 환경이면 `SET hnsw.iterative_scan = relaxed_order`" 조건부 적용은 **버전 확인 전까지 가정에 불과**하다.
> - `vector(1024)` 컬럼과 HNSW 인덱스는 pgvector 0.5.0 이상이면 사용 가능하나, **실제 버전을 M0에서 `SELECT extversion FROM pg_extension WHERE extname='vector'`로 확인해야 한다.**
> - `pgvectorscale`이 있다는 사실은 `ADR-002`(HNSW vs IVFFlat)의 선택지가 실은 3개였다는 뜻이다. HNSW를 유지하더라도 "pgvectorscale을 검토했고 이런 이유로 HNSW를 택했다"는 근거가 있어야 조사 깊이가 드러난다.

---

## 9. Trigger / Extension 일반 **[확정]**

- OpenSQL Database는 **PostgreSQL 16.8 그대로**이므로, 트리거·트리거 함수·파셜 유니크 인덱스·`FOR UPDATE SKIP LOCKED`·`ON DELETE CASCADE`·트랜잭셔널 아웃박스 패턴은 **전부 표준대로 동작**한다.
- 제약은 DB 계층이 아니라 **OpenProxy 경유 경로에서만** 발생한다 (§5, §6, §7).
- 즉 현재 설계의 **DB 계층 자동화 부분은 그대로 유효**하다. 문제는 애플리케이션이 DB에 어떻게 접속하느냐에 국한된다.

---

## 10. Driver **[확정 / 추론]**

- **[확정]** OpenProxy는 PostgreSQL 와이어 프로토콜을 그대로 말한다 (`psql` 예시가 문서에 있음). 인증은 `md5` | `scram-sha-256`.
- **[추론]** 따라서 psycopg3 / psycopg_pool을 그대로 사용할 수 있다. 드라이버 교체는 불필요하다.
- **[확정]** 문서에 클라이언트 드라이버별 특별 지침은 없다.
- **[미확인]** Failover 순간 in-flight 커넥션의 처리(끊김 여부, 재시도 필요성)는 문서에 기재되어 있지 않다. `shutdown_timeout`(기본 60000ms, "진행 중인 트랜잭션 완료 대기 시간")만 언급됨.

---

## 11. 로컬 개발 환경 **[확정]**

- **공식 Docker 배포판이 없다.** GitHub 조직의 `opensql3-docker` 리포지토리는 **README 17바이트짜리 빈 껍데기**다 (커밋 1개, 파일 1개).
- 공개된 리포지토리: `tibero-fdw`, `opensql3-docker`(빈), `version-automation-test`, `.github`, `orafce`, `postgresql-engineer-util`, `pg-ansible`, `release-automation`
- 즉 **로컬에서 OpenSQL 클러스터 전체(OpenHA + etcd + OpenProxy)를 재현할 공식 수단이 현재 없다.**

> **설계 영향**
> `ADR-007`(로컬은 pgvector 단일 컨테이너)은 결론 자체는 유지 가능하나, 근거가 바뀐다. "표준 PostgreSQL 기능만 쓰므로 단일 컨테이너로 95% 커버"가 아니라, **"OpenProxy 경유 경로(§5·§6·§7)는 로컬에서 검증 불가능하며, 이 갭이 M0의 검증 대상"**임을 명시해야 한다.

---

## 12. 미확인 항목 — 클러스터 확보 시 M0 검증 목록

우선순위 순:

| # | 검증 항목 | 방법 | 실패 시 영향 |
|---|---|---|---|
| 1 | **OpenProxy 경유 `LISTEN`/`NOTIFY` 동작 여부** (session/transaction 각각) | 워커 연결로 `LISTEN` 후 다른 세션에서 `NOTIFY` 발행 | 파이프라인 기동 방식 전면 재설계 |
| 2 | **PostgreSQL 메이저 버전** (16인가 14인가) | `SELECT version()` | pgvector 가용 버전, SQL 기능 범위 |
| 3 | **pgvector 버전** | `SELECT extversion FROM pg_extension WHERE extname='vector'` | HNSW·iterative_scan 가용성 결정 |
| 4 | HNSW 인덱스 생성 가능 여부 | `CREATE INDEX ... USING hnsw` 실행 | 인덱스 전략 변경 (IVFFlat 또는 pgvectorscale) |
| 5 | **제공 클러스터의 OpenProxy 실제 설정** | `SHOW CONFIG` | 읽기/쓰기 분리 활성 여부 → 검색 정합성 |
| 6 | LISTEN 연결의 `server_lifetime`/`idle_timeout` 실동작 | 장시간 유휴 LISTEN 유지 관측 | 워커 재연결 정책 |
| 7 | Failover 실측 소요 시간 | `patronictl switchover` + `failover` 각각 계측 | "무중단" 표현의 정확도 |
| 8 | Failover 중 in-flight 커넥션 처리 | 부하 중 전환하며 에러 관측 | 애플리케이션 재시도 정책 |
| 9 | pgvectorscale 사용 가능 여부 | `CREATE EXTENSION vectorscale` | 인덱스 대안 확보 |
| 10 | `max_connections` 여유 및 OpenProxy `pool_size` | `SHOW max_connections` + `SHOW CONFIG` | 풀 크기 산정 |

---

## 13. 기존 설계 문서에 미치는 영향 요약

### 반영 완료 (2026-08-04)

| 대상 | 조치 | 상태 |
|---|---|---|
| `ADR-001` | pg_cron 번들 사실 반영, "쓸 수 있지만 안 쓴다"로 근거 보강. 기동 방식은 ADR-009로 이관 | ✅ |
| `ADR-002` | pgvectorscale을 3번째 선택지로 명시, HNSW 미확인 리스크 기록 | ✅ |
| `ADR-006` | **전면 재작성** — OpenProxy VIP 단일 엔드포인트로 변경 | ✅ |
| `ADR-007` | 근거 교체 — "공식 Docker 부재 + OpenProxy 경로 검증 불가", 커버리지 표 추가 | ✅ |
| `ADR-009` | **신규** — 폴링 주 경로, NOTIFY 최적화 | ✅ |
| `ADR-010` | **신규** — 검색은 plain `BEGIN`으로 Primary 강제 (`READ ONLY` 금지) | ✅ |
| `CLAUDE.md` | CRITICAL 규칙 3개 교체/추가 (접속·검색 트랜잭션·폴링) | ✅ |
| `ARCHITECTURE.md` HA 절 | 재작성 — 3노드 구성도, 책임 분리표, Failover 시간, `max_connections` 제약 | ✅ |
| `ARCHITECTURE.md` 검색 SQL | `BEGIN…COMMIT` 추가 + `embedding_status` 필터 제거 | ✅ |
| `ARCHITECTURE.md` 워커 루프 | 폴링 주 경로, `content_hash` 재확인, `processing` 상태 반영 | ✅ |
| `ARCHITECTURE.md` 정합성 표 | 전달 보장·최신 수렴·읽기 정합성 행 갱신 | ✅ |
| `ARCHITECTURE.md` 시스템 개요도 | OpenProxy 계층 추가, 폴링/NOTIFY 우선순위 반영 | ✅ |
| `PRD.md` | "페일오버 무중단" → "짧은 중단 후 자동 복구" | ✅ |
| `UI_GUIDE.md` | 사용자 화면에서 노드 IP 제거, `/admin/status` 운영 화면 신설 | ✅ |
| `ADR-003` / `ADR-004` / `ADR-005` / `ADR-008` | 영향 없음 — 유지 | — |

### 설계 검토 지적사항 — 전건 반영 완료 (2026-08-04)

| # | 항목 | 해결 방식 |
|---|---|---|
| 1 | HNSW post-filter recall | `SET LOCAL hnsw.ef_search = 200` + 후보 `k*5` 과다 조회 (**ADR-011**). 비정규화는 에스컬레이션 경로로 문서화 |
| 2 | 검색 결과 문서 단위 중복 | `DISTINCT ON (document_id)`로 문서당 최고 청크 1건 (**ADR-011**) |
| 3 | INSERT 시 v1 이력 미기록 | 트리거 `on_document_content_changed()`가 잡 생성과 **같은 트랜잭션**에서 이력 기록. 앱은 `document_versions`에 직접 INSERT하지 않음 |
| 4 | 빈 파싱 결과 | 업로드 시 400 반환 + DB `CHECK (length(btrim(content)) > 0)` 이중 방어 |
| 5 | `error` 복구 경로 없음 | `POST /api/documents/{id}/reembed` — `UPDATE documents SET content_hash = content_hash`로 트리거 재발화. **`embedding_jobs` 직접 INSERT 규칙 유지** |
| 6 | MCP DB 라이프사이클 | 마이그레이션 러너를 `app/migrations.py`로 분리, API startup에서만 실행 (**ADR-012**). `db.py`는 import 부작용 없음 |
| 7 | `chunks.version` 미사용 | **정합성 검증 쿼리**의 근거 컬럼으로 활용 — `c.version <> d.version` 건수를 `/admin/status`에 노출해 "원본-벡터 정합성"을 수치로 증명 |

---

## 부록: 참고 URL

- 매뉴얼 인덱스: `https://docs.tibero.com/tmaxopensql/llms.txt`
- 개요: `https://docs.tibero.com/tmaxopensql/overview.md`
- 사전 요구사항·권장 구성: `https://docs.tibero.com/tmaxopensql/installation/prerequisites.md`
- 통합 설치(익스텐션 목록): `https://docs.tibero.com/tmaxopensql/installation/binary/opensql.md`
- DB 설치(PG 버전): `https://docs.tibero.com/tmaxopensql/installation/binary/opensql-database.md`
- Patroni 운영: `https://docs.tibero.com/tmaxopensql/administration/patroni.md`
- OpenProxy 개요: `https://docs.tibero.com/tmaxopensql/tmax-openproxy/openproxy.md`
- OpenProxy 설정 레퍼런스: `https://docs.tibero.com/tmaxopensql/tmax-openproxy/tmax-openproxy.md`
- 커넥션 풀: `https://docs.tibero.com/tmaxopensql/administration/openproxy/connection-pool.md`
- 로드밸런싱: `https://docs.tibero.com/tmaxopensql/administration/openproxy/load-balancing.md`
- VIP 이중화: `https://docs.tibero.com/tmaxopensql/administration/openproxy/virtual-ip-redundancy.md`
- OpenProxy 릴리즈 노트: `https://docs.tibero.com/tmaxopensql/releasenote/openproxy/openproxy-1.1.md`
- GitHub 조직: `https://github.com/tmaxopensql`

> 문서 사이트는 URL 뒤에 `.md`를 붙이면 마크다운 원문을, `?ask=<질문>`을 붙이면 질의응답을 제공한다.
