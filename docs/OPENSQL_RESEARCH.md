# OpenSQL 아키텍처 조사 결과

> 조사일: 2026-08-04 (같은 날 **실물 배포판 수령 후 개정**)
> 출처: Tmax OpenSQL 3.0 공식 매뉴얼 v1.5.0 (`docs.tibero.com/tmaxopensql`), Tmax OpenSQL GitHub 조직,
> **실제 배포 패키지 `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720`**
> 목적: `PROJECT_CONTEXT.md` 설계 원칙("OpenSQL 공식 아키텍처를 우선한다", "OpenSQL 기능을 애플리케이션에서 중복 구현하지 않는다")에 따라 조사 결과로 ADR·Architecture를 확정하기 위함

## 신뢰도 표기

| 표기 | 의미 |
|---|---|
| **[배포판]** | **실제 배포 패키지의 파일에서 직접 확인** (METADATA, 설정 템플릿, 바이너리). 가장 신뢰도가 높다 |
| **[확정]** | 공식 문서 본문에서 직접 확인 (설정 예제, 표, 버전 출력 등) |
| **[요약]** | 문서 사이트의 질의응답 기능을 통해 얻은 요약. 원문 그대로가 아닐 수 있음 |
| **[미확인]** | 공식 문서에 언급 자체가 없음. 실 환경에서 검증 필요 |

---

## 0. 배포판 수령으로 확정된 사항 (2026-08-04)

대회 사무국이 배포한 실제 패키지를 받아 **문서 조사만으로 알 수 없던 항목이 상당수 확정**되었다. 아래는 `METADATA` 원문이다.

```
[OPENSQL PACKAGE 3.0]
release 3.7
build 3.17.8.7
[SUPPORTED OS VERSION]
rockylinux 9.7
[INSTALLABLE BINARIES]
postgresql 17.8      etcd 3.6.5        patroni 4.0.5      openproxy v1.1.3
barman 3.11.1        opensql_cloud 0.0.0                  barman_agent 0.0.0
[PG EXTENSIONS]
postgis 3.5.4        pg_hint_plan 1.7.0  pgaudit 17.1     credcheck 4.2
system_stats 3.2     o2 1.4              opencrypto 1.0.0 pg_cron 1.6.7
pgvector 0.8.1       pgvectorscale 0.9.0 pg_profile 4.11  tibero_fdw 0.6.4
pg_repack 1.5.2
```

### 조사 결과가 틀렸던 항목

| 항목 | 문서 조사 결과 | **실제 배포판** | 영향 |
|---|---|---|---|
| **PostgreSQL** | 16.8 또는 14.13 ⚠️ | **17.8** — 둘 다 아니었다 | 로컬 컨테이너를 `pg17`로 (ADR-007). 문서 전반의 "PostgreSQL 16" 표기 수정 |
| **pgvector** | **[미확인]** — 문서 어디에도 없음 | **0.8.1** | HNSW·`iterative_scan`·`avg(vector)` 전부 사용 가능 (ADR-002, ADR-011, ADR-018) |
| pgvectorscale | 번들됨(버전 미확인) | `vectorscale` 0.9.0 | METADATA 이름을 그대로 SQL에 쓰면 실패 (§0 번들 확장 실측) |
| etcd | 3.5.6 / 3.5.21 | 3.6.5 | 영향 없음 |
| 패키지 예시 | `Tmax_OpenSQL_3.18.1.3` | `3.17.8.7` | 문서 예시 정정 |
| 확장 목록 | 12종 | **30종 이상** — METADATA 13종보다 많고 실제 SQL 이름도 다름 | 이름을 그대로 SQL에 쓰면 실패 (§0 번들 확장 실측) |

> **교훈**: `PROJECT_CONTEXT.md`의 "PostgreSQL 메이저 버전 확인"이 M0 최우선 항목 중 하나였는데, 문서에는 16.8과 14.13이 함께 언급되어 있어 둘 중 하나로 추정했다. **실제는 17.8로 후보에 없던 값이었다.** 문서 추정으로 설계를 고정하지 않고 미확인으로 표기해둔 것이 맞았다.

### 실 VM 실측 결과 **[실측 2026-08-05]**

`opensql-dev` VM(Rocky 9.7 x86-64, single 모드)에 실제로 설치해 측정했다. 설치 절차는 `SETUP_OPENSQL.md`.

> **`ADR-021`에 따라 이 표가 증거다.** 라이선스가 **2026-09-10에 만료**되면 PostgreSQL이 기동하지 않아 재측정이 불가능하다. 심사는 저장소 상태를 기준으로 하므로(규정 제11조 ②) 여기 남은 기록이 검증의 근거가 된다.

**METADATA 기록과 일치한 항목**

| 항목 | 문서 | 실측 |
|---|---|---|
| PostgreSQL | 17.8 | ✅ `17.8 on x86_64-pc-linux-gnu, gcc 11.5.0` |
| pgvector | 0.8.1 | ✅ `0.8.1` |
| vectorscale | 0.9.0 | ✅ `0.9.0` (METADATA 이름은 `pgvectorscale`) |
| `max_connections` | 100 | ✅ `100` |

**미확인이었다가 해소된 항목**

| # | 항목 | 결과 | 영향 |
|---|---|---|---|
| ② | HNSW 인덱스 생성 | ✅ `CREATE INDEX ... USING hnsw (v vector_cosine_ops)` 성공 | ADR-002 리스크 해제 |
| ③ | `avg(vector)` | ✅ 동작 | ADR-018 전제 실증 |
| ⑥ | `pg_trgm` | ✅ `CREATE EXTENSION` 성공 (번들 목록엔 없으나 contrib로 존재) | ADR-016 한국어 대안 확보 |
| ① | **OpenProxy 경유 `LISTEN`/`NOTIFY`** | ❌ **정정됨** — ~~동작~~. **유휴 세션에는 전달되지 않는다** (§7-3) | 리스크는 남아 있으나 **ADR-009가 폴링을 주 경로로 잡아 흡수한다** |

**문서 조사와 달랐던 것**

설치기가 생성한 `openproxy.toml`은 `pool_mode = "session"`이다(§5-1). 문서 기본값 `transaction`을 전제로 한 §7의 우려가 여기서 갈렸다. `query_parser_enabled = false`이고 백엔드가 primary 하나뿐이라 Replica 라우팅도 일어나지 않는다 — `ADR-010` 근거 교체가 실측으로 확인됐다.

**아직 측정하지 못한 것**

| 항목 | 사유 |
|---|---|
| ~~LISTEN 연결의 `idle_timeout` 실동작~~ | ✅ **M1 이후 측정됨** (§12 6번) — 유휴 세션은 끊기지 않았으나 애초에 알림이 오지 않는다. **폴링 주기 상향은 철회됐다** (§7-3) |
| ~~`avg`가 HNSW 인덱스를 타는지~~ | ✅ **M1 이후 측정됨** (§12 12·16·17번) — `avg`는 무죄. ~~문제는 벡터 정렬 서브쿼리 안의 JOIN이었다~~ → **17번 재측정에서 반증됨. JOIN은 막지 않는다.** 실제 변수는 `random_page_cost`였다 (16번) |
| Failover | ⛔ replica가 없어 리더 선출·승격은 불가. 다만 **PostgreSQL 프로세스 장애 자동 복구는 실측 완료** (아래 Single 장애 주입 실측, ADR-020) |

### Single 장애 주입 실측 [실측 2026-08-09]

**측정 조건**: 2026-08-09 00:12~00:22 KST, `opensql-dev`(192.168.64.4). Patroni
REST(8008)·etcd v3 HTTP(2379)·OpenProxy(6432)·PostgreSQL 직결(5432)을 0.5~1초 간격으로
동시에 찌르는 프로버의 결과를 VM의 `patroni.log`·`openproxy.log`와 대조했다.

**정지 상태에서 확인된 클러스터 파라미터**

| 항목 | 값 |
|---|---|
| Patroni | 4.0.5 · scope `opensql` · member `postgresql1` · role `primary` · **timeline 1** |
| 루프 파라미터 | `ttl=30` `loop_wait=10` `retry_timeout=10` **`failsafe_mode=true`** |
| `patronictl history` | **`[]`** — 이 클러스터는 전환을 한 번도 겪은 적이 없다 |
| etcd | 3.6.5 · 단일 멤버 · `leader`·`members/postgresql1`가 하나의 리스에 묶여 **TTL 30초** |

**시나리오 ① PostgreSQL `SIGKILL` — 감지 46 ms, 접속 재개 5.85초**

| 경과 | 사건 |
|---|---|
| 0 | postmaster `SIGKILL`. 백엔드 전멸 |
| **+46 ms** | Patroni `WARNING: Postgresql is not running.` |
| +233 ms | `INFO: starting primary after failure` |
| **+4.83 s** | `INFO: postmaster pid=...` |
| +5.78 s | crash recovery(redo) **3 ms** |
| **+5.85 s** | `이제 데이터베이스 서버로 접속할 수 있습니다` |

재기동 지연의 대부분은 감지가 아니라 옛 인스턴스 정리·재연결 확인 구간(+233 ms → +4.83 s)이다.

etcd는 이 구간에 아무 일도 겪지 않았다. `leader` 값은 한 번도 변하지 않았고 TTL 갱신도
끊기지 않았다. **PostgreSQL이 죽는 것과 리더가 바뀌는 것은 이 제품에서 완전히 분리된 사건이다.**

앱이 받은 예외는 `psycopg.errors.SystemError`이고 MRO가
`SystemError → OperationalError → DatabaseError → Error`라 **ADR-023의 재시도가 실제로 이
경로를 탄다.** `backend/app/api/retry.py`가 `psycopg.OperationalError`를 잡으므로 코드와도
일치한다. 5432 직결 쪽은 `OperationalError: connection failed: ... Connection refused`다.

**시나리오 ② etcd 정지 99초 — `failsafe_mode`가 primary를 지켰다**

| 경과 | 사건 |
|---|---|
| **+15.0 s** | `patroni_failsafe_mode_is_active` 0 → 1 |
| **+27.1 s** | `patroni_cluster_unlocked` 0 → 1 (`ttl=30` 만료와 정합) |
| 전 구간 | `patroni_primary=1`, **6432·5432 모두 쓰기 가능** |
| +99 s → +2.6 s | etcd 재기동 후 `leader` 키 복원, 플래그 전부 0 복귀 |

```text
ERROR: Error communicating with DCS
INFO: continue to run as a leader because failsafe mode is enabled and all members are accessible
```

`failsafe_mode`가 없었다면 Patroni는 `ttl` 만료 시점에 스스로를 강등해 읽기 전용으로
떨어뜨린다. 스플릿 브레인 방지가 목적이지만 **Single에서는 그 강등이 순수한 손해**라 배포판이
미리 막아 놨다. **DCS 장애가 곧 서비스 장애는 아니라는 실증이다.**

**시나리오 ③ Patroni만 `SIGKILL` — PostgreSQL은 멀쩡하고, 되살릴 주체가 없다**

| 경과 | 사건 |
|---|---|
| +0.9 s | Patroni REST(8008) 응답 없음 |
| **+23.9 s** | etcd에서 `leader`·`members/postgresql1` 키 소멸(잔여 TTL과 정합) |
| 전 구간 | **PostgreSQL은 6432·5432 모두 정상, 계속 쓰기 가능** |
| **+106 s** | **아무것도 Patroni를 되살리지 않았다** |

수동 재기동(`start_patroni.sh`) 시 락 재획득까지 10.1초이며 PostgreSQL은 재기동되지 않는다.
돌던 postmaster를 그대로 인수하고 `timeline`은 1로 유지된다.

**OpenProxy는 통보받지 않는다 — 실패해야 안다.** `openproxy.log`에서 확인된 동작은 셋이다.

1. 백엔드 축출은 요청이 실패한 순간에 일어나며 축출까지 **278 ms**였다. 헬스체크 타이머가 아니라 에러가 방아쇠다.
2. `pool_mode = "session"`이라 클라이언트 연결도 같이 끊긴다. 앱이 `OperationalError`를 보는 이유가 이것이다.
3. 재연결도 클라이언트 요청에 이끌려 일어난다.

> ⚠️ 로그의 재연결 성공 시각을 "OpenProxy의 복구 지연"으로 읽으면 안 된다. PostgreSQL이 접속을
> 수락하기 시작한 뒤 요청이 없던 공백이 섞여 있다. **OpenProxy의 복구 지연은 별도 값이 아니다.**

### 설치 실태 — 저장소 서술과 어긋나는 것 [실측 2026-08-09]

1. **WAL 아카이빙은 켜진 것처럼 보이지만 실질적으로 꺼져 있다.** `archive_mode = on`인데
   `archive_command = "/bin/true"`라 WAL을 보관하지 않는다. 따라서 DR(백업·PITR)은 동작하지
   않으며, barman을 "채택 비용 0"으로 본 판정도 Patroni 관리 설정 변경이 필요하다는 점에서
   전제가 흔들린다. **`archive_command`를 잘못 켜면 지금 도는 것이 깨진다** — 명령이 실패하면
   PostgreSQL이 해당 WAL을 지우지 않고 계속 쌓아 디스크가 찬다. `/bin/true`는 게으른 값이
   아니라 안전한 기본값이다.
   > **[실측 2026-08-10, #41] barman은 설치되어 있지 않다.** `~/Tmax_OpenSQL_.../barman/`에
   > tarball이 풀려만 있고 바이너리는 PATH 어디에도 없으며, `opensql-installer/` 전체에
   > barman 언급이 **0건**이다 — 설치 자동화가 다루지 않는 컴포넌트다. #25의
   > *"채택 비용이 구조적으로 0"*은 **코드 비용**을 말한 것이고 설치·SSH 키·서버 등록은 그대로
   > 남아 있다. **DR을 켜지 않기로 확정했다** (ADR-020 결정 6).

   관련 실측값 (2026-08-10): `wal_level = replica` · `archive_timeout = 0` ·
   `wal_keep_size = 1024` (MB) · `summarize_wal = off` (PG17 블록 증분 비활성) ·
   `pg_database_size('opensql')` = **8390 kB**
2. **이 설치의 OpenProxy에는 Patroni·etcd 연동이 없다.** `use_patroni`도 `[general.etcd]`도
   없고 `servers`에 primary 하나가 하드코딩돼 있다. 노드가 하나인 Single 구성 자체와 모순되지는
   않지만, ADR-006의 "새 프라이머리 발견·재연결은 OpenProxy가 수행한다"는 서술과 실물이
   어긋나므로 **ADR-006 정정 대상**이다.
3. **프로세스 감독도 일부만 구성돼 있다.** systemd 유닛은 `opensql-etcd.service` 하나뿐이고,
   Patroni·PostgreSQL·OpenProxy는 `nohup`으로 띄운 맨 프로세스다. Patroni를 죽인 뒤 106초 동안
   되살릴 주체가 없었고, Patroni watchdog도 `/dev/watchdog` 권한 부재로 비활성이다. 따라서
   **"HA 구성이 완전히 살아 있다"고 말하면 틀린다.**

배포판 `$OPENSQL_HOME/scripts/`에는 `finalize_single_to_ha.sh`가 있어 Single→HA 전환 경로 자체는
남아 있다. 그러나 **그 존재는 사무국의 Single 구성 지시를 어길 근거가 아니다.**

### 번들 확장 실측 — 이름과 개수가 METADATA와 다르다 [실측 2026-08-09]

실 VM의 preload 전체 목록은 다음과 같다.

```text
opensql=> SHOW shared_preload_libraries;
opensql_license, o2scheduler, pg_stat_statements, dbms_rls, pg_hint_plan,
pgaudit, pg_cron, dbms_alert, dbms_pipe, dbms_assert, dbms_output, credcheck
```

**`pg_cron`·`pgaudit`·`pg_hint_plan`·`pg_stat_statements`·`credcheck`는 이미 올라가 있다.**
이 5종은 preload 변경도 재시작도 필요 없다. 배포판이 미리 올려둔 것은 제품이 쓰라고 준 것이라는
신호로 읽어야 한다.

| METADATA (§0) | 실제 `pg_available_extensions` | 비고 |
|---|---|---|
| `pgvectorscale 0.9.0` | **`vectorscale` 0.9.0** | 이름이 다르다. `CREATE EXTENSION pgvectorscale`은 실패한다 |
| `o2 1.4` | `o2functions` 1.2 · `o2scheduler` 1.0 · `o2types` 1.1 · `o2views` 1.1 | **`o2`라는 확장은 없다.** 4개로 쪼개져 있다 |
| `system_stats 3.2` | `system_stats` **3.0** | 패키지 버전 ≠ 확장 SQL 버전 |
| `tibero_fdw 0.6.4` | `tibero_fdw` **1.0** | 위와 동일 |
| `postgis 3.5.4` | `postgis` + `postgis_raster` `postgis_sfcgal` `postgis_tiger_geocoder` `postgis_topology` `address_standardizer(_data_us)` | 6종으로 전개 |
| `opencrypto 1.0.0` | `opencrypto` 1.0.0 (**이름·버전 모두 일치**) | 이름이 같아 표에서 걸러졌으나 **내용이 이름에 드러나지 않는 경우**다. ARIA·SEED 국산 블록암호가 들어 있고, 이는 **출제자가 「기술 소개」에 직접 올린 항목**이다 |
| (목록에 없음) | `dbms_alert` `dbms_assert` `dbms_job` `dbms_output` `dbms_pipe` `dbms_random` `dbms_rls` `dbms_scheduler` `dbms_sql` `utl_file` | METADATA에 없는 10종이 더 있다 |
| (목록에 없음) | `opensql_license` 1.0 | 라이선스 검증 확장 |

즉 번들은 13종이 아니라 **30종 이상**이며, `o2`는 단일 확장이 아니라 Oracle 호환 스위트다.
실제 확장 이름은 [Tmax O2 Extensions 설치 문서](https://docs.tibero.com/tmaxopensql.en/tmax-o2-extensions/installation/o2-extension-installation)와
`pg_available_extensions` 결과를 기준으로 사용해야 한다. `dbms_scheduler`는 `o2scheduler` 확장 모듈에
의존하므로 설치할 때 `CASCADE`가 필요하다.

이름이 같은 확장도 실제 내용을 열어봐야 한다. `utl_file`·`dbms_scheduler`처럼 이름만으로 내용을
알 수 없고 아직 내부 기능을 확인하지 않은 확장이 남아 있으므로, 이름 불일치만 조사 축으로 삼으면
같은 누락이 반복된다.

`postgis`는 이 VM에서 설치 자체가 깨져 있다.

```text
ERROR: "/home/opensql/lib/postgis-3.so" 라이브러리를 불러 올 수 없음:
       libSFCGAL.so.2: 그런 파일이나 디렉터리가 없습니다
```

이는 우연한 별도 장애가 아니다. `SETUP_OPENSQL.md`가 설치 중 SFCGAL 요구사항을 우회하는 절차를
담고 있고 그 우회의 결과다. **`postgis`는 이 프로젝트에 접점이 없을 뿐 아니라 쓰려면 재설치부터
해야 한다.** 이 실측은 확장 채택을 뜻하지 않으며, 현재 마이그레이션은 계속 `vector`만 생성한다.

### opencrypto — ARIA·SEED 실측 [실측 2026-08-10]

`CREATE EXTENSION opencrypto` 한 줄로 설치되며 preload는 필요 없다. 제공 함수는 33개이고
`pgcrypto`와 함수 이름뿐 아니라 시그니처까지 같다.

**ARIA 구현은 실제 표준 벡터와 일치한다.** RFC 5794(KS X 1213)의 공식 시험벡터를 바이트 단위로
대조했으며 세 키 길이 모두 기대 암호문과 일치했다.

| 키 길이 | 기대 암호문 |
|---:|---|
| 128 | `d718fbd6ab644c739da95f3be6451778` |
| 192 | `26449c1805dbe7aa25a468ce263a9e79` |
| 256 | `f92bd7c79fb72e2f2b8f80c1972d24fc` |

**SEED의 `encrypt()`·`encrypt_iv()` 경로는 깨져 있다.** 모든 표기와 키 길이에서 실제 오류는
`Cipher cannot be initialized`였다. 이는 `No such cipher algorithm`과 다르다. 알고리즘 이름은
등록됐지만 암호 초기화 단계에서 실패한다는 뜻이다. 다음 세 가설은 실측으로 배제했다.

1. OpenSSL 3.5.1의 **default** provider에 `SEED-CBC`와 `SEED-ECB`가 존재한다.
2. `fips_enabled = 0`이다.
3. `postgres`와 `opencrypto.so`가 같은 `libcrypto.so.3`를 사용한다.

**PGP 경로는 동작하지만 표준 PGP가 아니다.** `pgp_sym_encrypt`는 미지원 값을 거부하므로 다른
알고리즘으로 조용히 폴백한 결과가 아니다. 패킷의 알고리즘 ID도 `aria = 0x0b`, `seed = 0x0e`로
구분된다. 그러나 두 ID는 RFC 4880 비표준이어서 표준 PGP 도구로 복호화할 수 없다.

채택했다면 다음 네 제약에 걸린다.

1. `pgcrypto`와 `digest`가 충돌하므로 같은 스키마에 공존할 수 없다. 별도 스키마에서는 가능하다.
2. `public.gen_random_uuid()`를 코어 함수와 중복 정의한다. 이 함수는
   `backend/migrations/002_tables.sql:12`에서 문서 ID 기본값으로 사용한다.
3. 로컬 `pgvector/pgvector:pg17` 컨테이너와 PGDG에 모두 없다. #28의 Dockerfile 해법이 통하지 않는
   첫 확장이다.
4. 국산 해시인 HAS-160·LSH는 없고 블록암호만 제공한다.

**채택하지 않는다.** 근거는 셋이다.

1. 추출 텍스트를 암호화하면 벡터 검색뿐 아니라 m9의 `pg_trgm` RRF와 검색 스니펫까지 동시에
   깨진다. `pg_trgm`은 #29가 `tsvector`를 버리고 고른 유일한 한국어 부분 일치 대안이다.
2. TDE가 아니라 컬럼 암호화이고 키가 SQL 인자다. DB가 키를 관리하지 않으므로 “DB 계층에서
   암호화”하는 구조가 성립하지 않는다.
3. 남는 적용처도 이미 기각한 논리에 걸린다. `password_hash`는 해시이지 암호화가 아니고,
   메타데이터 암호화는 화면에 드러나지 않으며(#29의 `pg_cron` 기각 논리), “민감 문서” 등급
   신설은 요구에 없다(#37의 워크스페이스 기각 논리).

AI 준비도 지도의 기준으로 재면 ARIA 컬럼 암호화는 외부 벡터 DB 구성에서도 RDBMS 쪽에서 똑같이
할 수 있다. 오히려 현재 구조에서는 암호문과 평문 벡터가 한 테이블에 나란히 놓여 불리하다.

### 대회용 구성 지시 **[배포판·메일 확정]**

사무국 안내 메일에 명시된 제약이다.

> "OpenSQL은 고가용성(HA) 구성이 아닌 **Single 구성**으로 설치해주시기 바랍니다.
>  여러 대의 서버를 연결하지 않고 서버 1대에 OpenSQL을 설치하시면 됩니다."
>
> "지원 OS : **Rocky Linux 9.7 전용**. Windows 환경은 지원하지 않으며, 다른 Linux 버전에서도 동작하지 않습니다."

**Single 모드도 4개 컴포넌트를 전부 설치한다** — 공식 가이드의 "클러스터 모드별 노드 역할" 표에 `single | PG + Patroni + etcd + OpenProxy`로 명시되어 있다. 따라서 OpenProxy 경유 경로(ADR-006·009·010)는 **그대로 검증 가능**하며, **실제 failover만 불가능**하다(승격 대상 replica 없음). 대응은 ADR-020.

### 아키텍처 **[배포판 확정]**

```
$ file openproxy/openproxy
  ELF 64-bit LSB pie executable, x86-64
$ file postgresql/bin/postgres
  ELF 64-bit LSB executable, x86-64
$ grep OPENSQL_RUST_TOOLCHAIN scripts/install.sh
  OPENSQL_RUST_TOOLCHAIN="1.85.0-x86_64-unknown-linux-gnu"
```

바이너리가 x86-64이고 설치 스크립트에도 x86_64가 하드코딩되어 있다. **Apple Silicon에서 aarch64 VM + Rosetta로 우회할 수 없다.** 환경 구축 절차는 `SETUP_OPENSQL.md`.

### 라이선스 **[배포판 확정]**

```xml
<identified_by_host>opensql-dev</identified_by_host>   <!-- 검증 기준: hostname -->
<limit_cpu>4</limit_cpu>                                <!-- CPU 상한 -->
<end_date>2026/09/10</end_date>                         <!-- 만료 -->
<edition>Enterprise</edition>  <type>trial</type>
```

- 검증은 **hostname과 CPU 상한**으로 이루어진다. 아키텍처·OS 필드는 없다
- `patroni.yml`의 `shared_preload_libraries`에 **`opensql_license`가 포함**되어, 라이선스가 맞지 않으면 PostgreSQL이 기동하지 않는다. 이는 preload된 12개 중 하나이며 전체 목록은 위 「번들 확장 실측」에 기록했다
- 배치 위치: `opensql-installer/licenses/`, 파일명은 `config/common.env`의 `LICENSE_NAME`으로 지정
- **만료일(2026/09/10)이 대회 일정과 겹치는지 확인이 필요하다.** 이후에는 DB를 띄울 수 없다

---

## 1. 구성요소

**[확정]** OpenSQL v3.0은 단일 DBMS가 아니라 **4개 컴포넌트로 구성된 클러스터 제품**이다.

| 컴포넌트 | 기반 기술 | 버전 **[배포판]** | 역할 |
|---|---|---|---|
| **OpenSQL Database** | PostgreSQL | **17.8** | 데이터 노드 |
| **OpenHA Cluster Manager** | Patroni | **4.0.5** | 노드 상태 감시, 자동 Failover, Primary 선출 |
| **OpenHA DCS** | etcd | **3.6.5** | 클러스터 멤버십·구성 정보 분산 저장 |
| **OpenProxy** | Rust 자체 구현 | **1.1.3** | 커넥션 풀링, 로드밸런싱, 읽기/쓰기 분리, VRRP VIP Failover |
| Barman | Python | 3.11.1 | 백업/복구 (전체·증분·차등) |

> ✅ **PostgreSQL 메이저 버전은 17이다.** 문서에는 16.8과 14.13이 함께 언급되어 둘 중 하나로 추정했으나, 실제 배포판은 **후보에 없던 17.8**이었다 (§0). 로컬 개발 컨테이너도 `pg17`로 맞춘다.

### 번들 익스텐션 목록 **[배포판]**

통합 설치 시 함께 설치되는 구성요소:

**Core**: `postgresql 17.8`, `etcd 3.6.5`, `patroni 4.0.5`, `openproxy 1.1.3`

| 확장 | 버전 | | 확장 | 버전 |
|---|---|---|---|---|
| **pgvector** | **0.8.1** | | pg_cron | 1.6.7 |
| **pgvectorscale** | **0.9.0** | | pg_hint_plan | 1.7.0 |
| postgis | 3.5.4 | | pg_profile | 4.11 |
| pgaudit | 17.1 | | pg_repack | 1.5.2 |
| credcheck | 4.2 | | tibero_fdw | 0.6.4 |
| system_stats | 3.2 | | o2 | 1.4 |
| opencrypto | 1.0.0 | | | |

> ⚠️ **이 표는 `METADATA` 원문이며 `CREATE EXTENSION`에 쓸 이름이 아니다.** 실제 확장 이름과
> 개수는 §0의 「번들 확장 실측」을 보라 — `pgvectorscale`이 아니라 `vectorscale`이고, `o2`는 없다.

> **설계 영향 (중요)**
> - **`pgvector 0.8.1`이 확정되어 미확인 리스크가 해소됐다.** HNSW(0.5.0+), `hnsw.iterative_scan`(0.8+), `avg(vector)`(0.5.0+)이 모두 사용 가능하다. `ADR-002`의 HNSW 리스크, `ADR-011`의 조건부 적용, `ADR-018`의 `avg` 가용성이 전부 확정으로 바뀐다.
> - **`vectorscale` 0.9.0도 번들**이다(METADATA 표기는 `pgvectorscale`). StreamingDiskANN 인덱스를 제공하며 HNSW와 다른 특성을 가진다. `ADR-002`에서 대안으로 검토했고 데모 규모에서는 HNSW를 유지한다.
> - **`pg_cron`은 기각한다.** 이미 preload되어 있어 쓸 수 있지만, #29의 판정대로 사용자 화면에 보이지 않는 내부 개선이며 이 프로젝트의 판단 기준인 *"외부 벡터 DB를 붙였다면 못 했을 일을 하고 있느냐"*를 충족하지 않는다. "좀비 회수가 죽는 프로세스 안에 있어 자기 잡을 살리지 못하므로 `pg_cron`이 필요하다"는 판정은 틀렸다. `sweep_zombies()`는 워커 신원과 무관한 전역 스윕이고 루프 머리에 있어 워커 재기동 첫 반복에서 즉시 회수한다. 따라서 기각 근거를 이 오해에 두지 않는다. 즉 "쓸 수 없어서"가 아니라 **"쓸 수 있지만 안 쓴다"**이다.
> - `pg_repack`은 기존 문서 조사에서 누락됐던 항목이다. 이 프로젝트에서 쓰지 않는다.

### 설치 방식 **[배포판]**
- Python 기반 설치기: `opensql_local_installer.py`(현재 노드) / `opensql_remote_installer.py`(SSH 중앙 배포)
  ```bash
  cd opensql-installer
  python3 opensql_local_installer.py --mode single    # single | 2node-witness | 3node
  ```
- 스크립트 개별 설치도 가능: 환경변수(`OPENSQL_HOME`, `PG_HOME`, `PG_DATA_DIR`) → `setenv.sh` → `install.sh rpm postgresql` → `install.sh extension pgvector`
- 설정 파일: `config/common.env`(공통), `config/remote.env`(원격 전용), `config/patroni.config.env`, `config/openproxy.config.env`, `config/etcd.config.env`
- 필요 포트: **5432**(PG), **6432**(OpenProxy), 6433(OpenProxy 관리), 2379·2380(etcd), 8008(Patroni REST)
- 설치 중 `sudo` 권한 필요. `ENABLE_SERVICE`·`GRANT_OPENSQL_SUDO`로 조정 가능
- 실제 수령 패키지: `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720`

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

> ~~`server_lifetime`(1시간)과 `idle_timeout`(10분)은 **워커의 장수 LISTEN 연결에 직접 영향을 준다.** OpenProxy를 경유하면 LISTEN 연결이 최대 1시간마다 강제로 끊길 수 있다 (§7과 연결).~~
>
> **정정 (2026-08-05 재실측)** — 두 정책 모두 발동하지 않았다. 유휴 세션이 **70분간 유지**되고 backend pid도 불변이었다. `pool_mode = "session"`에서는 백엔드가 클라이언트에 고정되어 풀로 반납되지 않으므로 정책이 발동할 기회가 없는 것으로 보인다 (§12 6번). 단, 관측이 체크포인트마다 쿼리를 보냈으므로 **무활동 최대 구간은 15분**이다.

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

**실측 정정 (2026-08-09)**: 위 내용은 공식 제품 기능에 대한 설명이며, 현재 Single 설치의 실제
구성과는 다르다. 이 설치의 `openproxy.toml`에는 `use_patroni`와 `[general.etcd]`가 없고,
`servers`에 primary 한 대가 하드코딩돼 있다. 따라서 이 OpenProxy는 **정적 서버 목록을 가진 순수
커넥션 풀러**이며 새 프라이머리를 발견하는 경로가 구성되어 있지 않다. PostgreSQL `SIGKILL`
실측에서도 백엔드 축출은 헬스체크 통보가 아니라 **클라이언트 요청이 실패한 순간** 일어났고,
재연결도 다음 클라이언트 요청에 이끌려 일어났다. 공식 기능을 삭제할 이유는 없지만, 이 설치에서
구성하지 않은 기능을 사용 중이라고 서술해서는 안 된다. ADR-006 정정은 후속 step에서 이 근거를
인용한다.

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

### 5-1. 실측 — 설치기는 `session`으로 생성한다 **[실측 2026-08-05]**

**"기본값이 `transaction`"은 설정 파일을 직접 작성할 때의 이야기다.** `opensql_local_installer.py --mode single`이 생성한 `$OPENSQL_HOME/etc/openproxy/openproxy.toml`은 다음과 같다.

```toml
[general]
host = "0.0.0.0"
port = 6432
admin_port = 6433
admin_username = "postgres"
admin_password = "pg_password"
connect_timeout = 10000

[pools.opensql]
pool_mode = "session"            # ← transaction 이 아니다
default_role = "primary"
query_parser_enabled = false     # ← 읽기/쓰기 분리 꺼짐

[pools.opensql.users.0]
username = "postgres"
password = "pg_password"
pool_size = 10

[pools.opensql.shards.0]
servers = [
    ["192.168.64.4", 5432, "primary"],
]
database = "postgres"
```

| 항목 | 문서 기본값 | 설치기 생성값 |
|---|---|---|
| `pool_mode` | `transaction` | **`session`** |
| `query_parser_enabled` | `false` | `false` (명시) |
| `default_role` | — | `primary` (명시) |
| `pool_size` | — | 10 |
| `database` | — | **`postgres`** → 2026-08-06 `opensql`로 교정 (아래) |

> **⚠️ 설치기는 `opensql` 데이터베이스를 만들어놓고 풀은 `postgres`를 바라보게 설정한다** (2026-08-06 발견).
>
> 풀 이름과 백엔드 데이터베이스 이름이 다르다. 클라이언트는 DSN의 dbname 자리에 **풀 이름**(`opensql`)을
> 적으므로, 실제로 어느 데이터베이스에 쓰는지가 드러나지 않는다. 그래서 마이그레이션과 애플리케이션
> 데이터가 관리용 기본 DB인 `postgres`에 쌓이고 있었고, 정작 `opensql` DB는 빈 채로 남아 있었다.
>
> `postgres`·`template0`·`template1`은 OID 1·4·5로 `initdb`가 만드는 PostgreSQL 기본 DB이고,
> `opensql`은 OID 16387·소유자 `opensql` 롤로 설치기가 추가한 것이다.
>
> ```toml
> [pools.opensql.shards.0]
> database = "opensql"    # ← "postgres" 에서 변경
> ```
> `bash $OPENSQL_HOME/scripts/restart_openproxy.sh` 후
> `psql ... -d opensql -c "SELECT current_database()"`가 `opensql`을 반환하면 반영된 것이다.
> `reload`(SIGHUP)는 이미 열린 백엔드 연결이 옛 DB를 향한 채 재사용될 수 있어 `restart`를 쓴다.
> **`DATABASE_URL`은 바뀌지 않는다** — 풀 이름은 그대로다.

> **설계 영향 두 가지**
>
> 1. **~~§7의 LISTEN/NOTIFY 리스크가 해소된다.~~ → 정정됨.** `session` 모드라 세션 상태가 보존되는 것은 맞지만, **유휴 세션에 알림이 전달되지 않는 문제는 그대로다** (§7-3). 워커는 폴링을 주 경로로 유지한다 (ADR-009).
> 2. **`ADR-010`의 근거 교체가 옳았음이 확인된다.** `query_parser_enabled = false`이고 `servers`에 primary 하나뿐이라 Replica 라우팅이 일어날 수 없다. 명시적 트랜잭션을 유지하는 이유는 이제 "Replica 라우팅 방지"가 아니라 **`SET LOCAL` 보장 + HA 전환 대비**다.
>
> **주의**: `pool_size = 10`이고 `max_connections = 100`이다. 애플리케이션 풀(API + 워커)을 이 안에서 산정해야 한다.

> ⚠️ **접속 시 `-d`에 pool 이름을 넣는다.** DB 이름(`postgres`)이 아니라 pool 이름(`opensql`)이다 — OpenProxy 규약(§4).
> ```bash
> psql -h <VM_IP> -p 6432 -U postgres -d opensql     # 비밀번호 pg_password
> ```

### 5-2. 실측 — 백엔드를 넘길 때 세션 상태를 **부분만** 초기화한다 **[실측 2026-08-05]**

`session` 모드는 백엔드를 클라이언트 접속 동안 전용으로 유지한다. 문제는 **그 클라이언트가 떠난 뒤**다.
다음 클라이언트가 같은 백엔드를 물려받을 때 무엇이 남아 있는지 측정했다.

세션1이 `LISTEN scope_ch` + `CREATE TEMP TABLE scope_tmp` + `SET statement_timeout='4321ms'`를
남기고 종료한 뒤, 세션2가 같은 풀에 접속했다(같은 backend pid).

| 세션 상태 | 결과 |
|---|---|
| GUC (`SET statement_timeout`) | **초기화됨** ✅ (`'0'`으로 복원) |
| `LISTEN` 등록 | **누수** ❌ |
| 임시 테이블 | **누수** ❌ |

`LISTEN`을 한 번도 걸지 않은 새 세션이 `pg_listening_channels()`에서 이전 세션들의 채널을
그대로 관측했다(6회 연속 재현). 노드 직결 5432에서는 매번 새 백엔드가 배정되어 빈 목록이다.

> **즉 OpenProxy는 `RESET ALL`은 하지만 `DISCARD ALL`은 하지 않는다.**

**설계 영향 두 가지**

1. **`ADR-010`·`ADR-011`이 `SET LOCAL`을 쓰기로 한 결정이 실측으로 정당화된다.** 트랜잭션 범위
   설정은 커밋 시 복원되며 다음 클라이언트로 새지 않음을 확인했다. GUC는 프록시도 초기화하므로
   이중 안전이다.
2. **임시 테이블을 쓰지 않는다.** 남의 세션이 만든 테이블이 이름 그대로 보이는 환경이라, 중간
   결과를 임시 테이블에 담으면 **이름 충돌과 낡은 데이터 참조가 에러 없이 일어난다.**
   검색·추천의 중간 결과는 CTE로 처리한다 (`CLAUDE.md` 아키텍처 규칙).

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

### 배포판 확인 결과 — Single 템플릿에는 읽기/쓰기 분리가 없다 **[배포판]**

`openproxy/openproxy.standalone.template.toml`(single 모드 전용)의 전체 pool 설정이다.

```toml
[pools.FILL_ME]
pool_mode = "transaction"
auth_type = "scram-sha-256"

[pools.FILL_ME.shards.0]
servers = [
    ["FILL_ME", 5432, "primary"],    # primary 1개
    # ["FILL_ME", 5432, "replica"],  # replica는 주석 처리
]
```

**`query_parser_enabled`·`query_parser_read_write_splitting`·`primary_reads_enabled`가 아예 없다.** 기본값이 `false`이므로 읽기/쓰기 분리가 일어나지 않고, 애초에 라우팅 대상 replica도 없다.

> **설계 영향 (ADR-010 근거 교체)**
>
> ADR-010의 원래 근거 — *"트랜잭션 밖 단순 SELECT가 Replica로 라우팅되어 복제 지연으로 방금 임베딩된 청크가 누락된다"* — 는 **Single 구성에서 성립하지 않는다.** Replica가 없다.
>
> **그러나 결론(명시적 트랜잭션)은 유지한다.** 근거가 둘로 바뀐다.
> 1. **`pool_mode = "transaction"`이 템플릿에 명시되어 있다.** 이 모드에서 트랜잭션 밖의 `SET`은 의도대로 동작하지 않는다(§5). `SET LOCAL hnsw.ef_search`(ADR-011)를 안전하게 쓰려면 명시적 트랜잭션이 **필요조건**이다.
> 2. `scripts/finalize_single_to_ha.sh`가 존재한다. 나중에 HA로 전환하면 원래 근거가 그대로 되살아난다. 지금 트랜잭션으로 감싸두면 전환 시 코드를 고칠 필요가 없다.
>
> 즉 "Replica 라우팅 방지"에서 **"세션 상태(`SET LOCAL`) 보장 + HA 전환 대비"**로 근거가 바뀐다.

---

## 7. LISTEN / NOTIFY **[실측 완료]** ⚠️ 결론 정정됨 (2026-08-05 재실측)

> **결론 먼저**: **OpenProxy(6432) 경유로는 유휴 세션을 깨우지 못한다.**
>
> 알림이 유실되지는 않는다. 그러나 **클라이언트가 다음에 쿼리를 보낼 때까지 전달이 지연된다.**
> 워커의 `LISTEN`은 유휴 상태에서 깨어나기 위한 장치이므로, 깨우려면 먼저 쿼리를 보내야 한다면
> **실질적으로 무효**다.
>
> 같은 날 먼저 기록한 §7-2("정상 동작한다")는 **psql 대화형 세션으로 측정한 결과**이며,
> 유휴 상태의 비동기 전달을 검증하지 못했다. 경위·재실측·설계 영향은 **§7-3**.
>
> §7-1의 우려("`transaction` 모드라 동작하지 않을 것")는 여전히 근거가 틀렸다 — 실제 설치본은
> `session`이다(§5-1). 다만 **`session` 모드라고 해서 비동기 전달이 보장되지는 않는다**는 것이
> 이번 재실측의 요지다.

### 7-1. 문서 조사 단계의 우려 (기록 보존)

**공식 문서 전체에 `LISTEN`/`NOTIFY`에 대한 언급이 단 한 줄도 없다.** 지원한다고도, 안 한다고도 쓰여 있지 않다.

- DB 계층(PostgreSQL 17)에서는 당연히 표준 동작한다
- 문제는 **OpenProxy를 경유할 때**다
- `LISTEN`은 세션 상태이므로, 이론적으로 `transaction` 모드에서는 동작하지 않을 가능성이 매우 높다 (PgBouncer 계열의 알려진 제약과 동일 구조)
- `session` 모드에서는 동작할 가능성이 있으나 **문서로 확인되지 않음**

> **설계 영향 (치명적)**
> 현재 `ARCHITECTURE.md`는 `pg_notify` → 워커 `LISTEN`을 **주 경로**로, 10초 폴링을 **안전망**으로 설계했다. 이 주 경로가 검증되지 않은 가정 위에 서 있다.
>
> **M0 최우선 검증 항목.** 결과에 따라 두 갈래:
> - 동작함 → 워커 LISTEN 연결만 `session` 모드 전용 pool 또는 노드 직결로 분리
> - 동작 안 함 → 폴링을 주 경로로 승격하고 NOTIFY를 최적화로 격하 (설계 서사 수정)

### 7-2. 1차 실측 **[2026-08-05 · 결론 정정됨 → §7-3]**

> ⚠️ **이 절의 "동작한다"는 결론은 §7-3에서 뒤집혔다.** 측정 방법의 한계가 어떻게 잘못된 결론을
> 만들었는지 남기기 위해 원문을 그대로 보존한다. 아래 내용을 근거로 삼지 말 것.

**동작한다.** 위 두 갈래 중 "동작함"이었다. 그러나 **`ADR-009`의 결론(폴링 주 경로)은 바꾸지 않는다.**

| | 실측 전 가정 | 실제 |
|---|---|---|
| `pool_mode` | `transaction` (문서 기본값) | **`session`** — 설치기가 그렇게 생성 |
| LISTEN 수신 | 불가능할 것 | **정상 수신** (페이로드 포함) |

**결론을 유지하는 이유 — 동작 여부와 전달 보장은 다른 문제다.**

1. **연결이 끊긴 구간의 알림은 유실된다.** `NOTIFY`는 커밋 시 발행되고 그때 연결이 없으면 사라진다. 재연결까지의 공백에 발행된 잡은 폴링만이 회수한다
2. **`idle_timeout`(기본 10분)·`server_lifetime`(기본 1시간)이 그대로다.** LISTEN 연결은 본질적으로 유휴 상태라 주기적으로 끊긴다 (§4)
3. `ADR-009`의 핵심 주장은 *"기동 방식은 정합성의 일부가 아니다"*이다. 아웃박스와 `SKIP LOCKED`가 유실·중복을 막으므로, LISTEN이 되든 안 되든 **정합성 서사는 흔들리지 않는다**

**바뀌는 것: 폴링 주기.** LISTEN이 즉시 깨워주므로 **5초 → 30초로 늘려도 체감 지연이 없다.** 폴링은 안전망으로 남고 DB 부하는 6분의 1이 된다. 이것이 ADR-009가 예고한 *"동작하면 폴링 주기를 늘려 부하를 낮춘다"*의 실행이다.

> ⚠️ **미검증 항목이 남아 있다.** LISTEN 연결을 10분 이상 유휴로 두었을 때 실제로 끊기는지는 관측하지 못했다. 폴링 주기를 늘리기 전에 이것을 확인해야 한다 — 끊긴다면 워커의 재연결·재등록 로직이 정상 동작하는지가 전제다.

### 7-3. 재실측 — 유휴 세션에는 전달되지 않는다 **[실측 2026-08-05, M1 이후]**

§7-2가 남긴 미검증 항목(유휴 LISTEN)을 확인하려다 **1차 결론 자체가 틀렸음**을 발견했다.

**측정**: 동일한 psycopg 스크립트로 두 경로를 비교했다. 리스너를 열어 `LISTEN` 후 아무것도 하지 않고, 별도 커넥션에서 `NOTIFY`를 발행한 뒤 수신 여부를 본다.

| 경로 | 유휴 중 비동기 수신 | 클라이언트가 쿼리를 보낸 뒤 |
|---|---|---|
| 노드 직결 5432 | **`['X']`** ✅ | `[]` (이미 받았다) |
| **OpenProxy 6432** | **`[]`** ❌ (30초를 기다려도 0건) | **`['X']`** |

**알림은 유실되지 않는다.** OpenProxy가 쥐고 있다가 클라이언트의 다음 상호작용 시점에 밀어낸다.

**왜 1차 측정이 "동작한다"로 보였나 — 증거는 이 문서 안에 이미 있었다.**

§12의 재현 명령에 이렇게 적혀 있다.

```
opensql=> LISTEN ch1;
opensql=> SELECT 1;          -- 알림은 다음 쿼리 실행 시 표시된다
```

**그 `SELECT 1`이 바로 지연된 알림을 밀어낸 트리거였다.** 1차 측정은 이 동작을 목격하고도 psql의
출력 관례로 해석했다. 대화형 psql은 사용자가 계속 입력을 보내므로, 프록시가 알림을 쥐고 있어도
사람 눈에는 즉시 도착하는 것처럼 보인다. **유휴 클라이언트를 재현하지 않은 것이 원인이다.**

**유휴 세션 생존은 별개로 확인했다 (70분 관측).**

체크포인트 1·6·11·16·25·40·55·62·70분에서 세션이 모두 살아 있었고, backend pid가 70분 내내
동일했으며 `LISTEN` 등록도 유지됐다.

| 문서값 | 실측 |
|---|---|
| `idle_timeout` 10분 | **끊지 않는다** — 무활동 15분 구간(25→40, 40→55분)을 통과했다 |
| `server_lifetime` 60분 | **교체하지 않는다** — 70분 동안 backend pid 불변 |

`session` 풀링에서는 백엔드가 클라이언트에 고정되어 풀로 반납되지 않으므로, 수명·유휴 정책이
발동할 기회 자체가 없는 것으로 보인다.

> ⚠️ **검증 범위의 한계를 명시한다.** 프로브는 체크포인트마다 `SELECT`를 보냈으므로 **무활동
> 최대 구간은 15분**이다. "70분 내내 한 번도 건드리지 않은 연결"은 검증하지 않았다.
> 1차 측정이 실패한 것과 **같은 종류의 한계**이므로, 덮지 않고 남긴다.

> ⚠️ **`_listen_for_jobs`의 재등록 백오프는 여전히 미검증이다.** 이슈 #16 절차 3은 "끊긴 뒤
> 재등록이 실제로 복구하는지"를 요구했으나, **연결이 끊기지 않아 전제가 성립하지 않았다.**
> 테스트(`test_listen_wakes_the_worker_on_the_trigger_notify`)가 덮는 것은 로컬 컨테이너
> 직결에서의 **최초 등록과 수신뿐**이며, 끊긴 뒤 재등록 경로는 테스트도 실측도 없다.
>
> 다만 **폴링이 주 경로이므로 이 경로가 조용히 죽어도 파이프라인은 정상 동작한다** (ADR-009).
> 그래서 이 공백은 정합성 리스크가 아니라 최적화 리스크다 — 알림이 끊기면 지연이 폴링 주기(5초)로
> 돌아갈 뿐이다. 위 15분 한계와 마찬가지로, 확인하지 않은 것을 확인한 것처럼 두지 않기 위해 남긴다.

**설계 영향**

1. **`ADR-009`의 "폴링 주기 5→30초 상향"을 철회한다.** LISTEN이 유휴 워커를 깨우지 못하므로
   30초는 그대로 30초 지연이다. **5초를 유지한다.**
2. **`ADR-009`의 결론(폴링 주 경로)은 오히려 강화된다.** "기동 방식은 정합성의 일부가 아니다"라는
   설계가 이 상황을 정확히 막았다. `_listen_for_jobs`가 실패해도 파이프라인이 도는 구조라
   **코드 변경이 필요 없다.**
3. **`_listen_for_jobs`를 제거하지 않는다.** 노드 직결 환경(로컬 컨테이너·개발)에서는 정상
   동작하며, OpenProxy 경유에서만 이득이 없다.

---

## 8. pgvector / HNSW **[일부 확정, 일부 미확인]**

**[배포판]으로 전부 해소됨.** 문서 조사 단계에서는 버전이 어디에도 없어 최대 리스크였으나, METADATA에서 확정되었다.

| 항목 | 상태 |
|---|---|
| pgvector **버전** | **[배포판] 0.8.1** |
| `vectorscale` 버전 | **[실측] 0.9.0** (METADATA 표기는 `pgvectorscale`, StreamingDiskANN 제공) |
| **HNSW 인덱스 지원** | ✅ 사용 가능 — 0.5.0+ 요구, 0.8.1이므로 충족 |
| `hnsw.iterative_scan` | ✅ 사용 가능 — **0.8+ 요구, 0.8.1이므로 충족** |
| `avg(vector)` 집계 | ✅ 사용 가능 — 0.5.0+ 요구 |

> **설계 영향**
> - `ADR-002`의 "HNSW 인덱스 사용 가능 여부 미확인" 리스크가 **해제**된다. `ARCHITECTURE.md`의 ⚠️ 경고도 제거한다.
> - `ADR-011`의 "`hnsw.iterative_scan`은 0.8+ 전용인데 버전 미확인이므로 의존할 수 없다"는 **더 이상 유효하지 않다.** 필터 결합 검색의 recall 부족에 대해 `ef_search` 확대 외에 `iterative_scan = relaxed_order`라는 정공법을 쓸 수 있다.
> - `ADR-018`(관련 문서를 질의 시점 `avg(embedding)`으로)의 기능 가용성이 확정된다. 다만 **`avg` 결과가 HNSW 인덱스를 타는지는 플래너 문제라 여전히 실측 대상**이다 (§12-12).
> - 인덱스 생성 자체는 실제로 실행해 확인한다 — 버전이 맞아도 빌드 옵션 등으로 막힐 가능성은 남는다.

---

## 9. Trigger / Extension 일반 **[확정]**

- OpenSQL Database는 **PostgreSQL 17.8 그대로**이므로, 트리거·트리거 함수·파셜 유니크 인덱스·`FOR UPDATE SKIP LOCKED`·`ON DELETE CASCADE`·트랜잭셔널 아웃박스 패턴은 **전부 표준대로 동작**한다. 우리가 쓰는 기능 중 PG16→17에서 동작이 바뀐 것은 없다.
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

### 배포판 수령 후 (2026-08-04) **[배포판]**

**Docker 이미지는 여전히 없지만, 설치 바이너리를 받아 가상머신에 직접 설치할 수 있게 되었다.** 즉 "OpenSQL을 로컬에서 돌릴 수단이 없다"는 상태는 해소됐다.

| | 상태 |
|---|---|
| 공식 Docker 이미지 | ❌ 여전히 없음 (`opensql3-docker`는 빈 껍데기) |
| 설치 바이너리 | ✅ 수령 (`Tmax_OpenSQL_3.17.8.7_rockylinux9.7`) |
| 실행 환경 | **Rocky Linux 9.7 x86-64 전용.** Apple Silicon에서는 QEMU 전체 에뮬레이션 VM 필요 (§0) |

### 로컬 컨테이너와 실 VM의 확장 차이 **[실측 2026-08-10]**

- 로컬 `pgvector/pgvector:pg17` 컨테이너는 `vector` **0.8.6**이며 `pg_trgm` **1.6을 사용할 수
  있다.** `pg_trgm`은 PostgreSQL contrib이므로 이를 위해 별도 이미지가 필요하지 않다.
- 실 VM의 `vector`는 **0.8.1**이다.
- 로컬 컨테이너에는 `pg_cron`과 `vectorscale`이 없다. 따라서 이 둘의 OpenSQL 고유 동작은 로컬
  환경에서 검증할 수 없다.

**개발 환경은 두 갈래로 유지한다.**

- **일상 개발·테스트**: `pgvector/pgvector:pg17` 단일 컨테이너. 트리거·아웃박스·`SKIP LOCKED`·검색 SQL은 전부 여기서 검증된다
- **OpenSQL 고유 동작 확인**: VM. OpenProxy 경유 경로(§5·§6·§7), 라이선스, 번들 확장 실동작

x86-64 에뮬레이션은 네이티브의 1/10~1/20 속도라 임베딩 워커까지 VM에 넣으면 실습이 불가능하다. **DB만 VM에 두고 애플리케이션은 맥 네이티브로 두는 구성**을 쓴다. 절차는 `SETUP_OPENSQL.md`.

> 로컬 컨테이너 태그를 `pg16` → **`pg17`**로 바꾼다. 실제 OpenSQL이 PostgreSQL 17.8이므로 메이저 버전을 맞춰야 한다 (ADR-007).

---

## 12. M0 검증 목록

배포판 수령(§0)으로 여러 항목이 해소되었다. 남은 것은 **실제 기동 후에만 알 수 있는 동작**이다.

### ✅ 배포판으로 해소된 항목

| # | 항목 | 결과 |
|---|---|---|
| 2 | PostgreSQL 메이저 버전 | **17.8** — 추정했던 16·14 둘 다 아니었다 |
| 3 | pgvector 버전 | **0.8.1** |
| 4 | HNSW 지원 | 0.5.0+ 요구 → 충족 (생성 실행은 아래 재확인) |
| 9 | pgvectorscale | **0.9.0** 번들 확인 |
| 11 | `avg(vector)` 지원 | 0.5.0+ 요구 → 충족 |
| 5 | OpenProxy 설정 | **성격 변경** — "제공받는 클러스터 설정 확인"이 아니라 **우리가 직접 작성**한다. `openproxy.standalone.template.toml`에는 `query_parser_read_write_splitting`이 아예 없고 `servers`도 primary 1개뿐이다 (§6 참조) |

### ✅ 실 VM에서 측정 완료 (2026-08-05)

| # | 검증 항목 | 결과 |
|---|---|---|
| 1 | **OpenProxy 경유 `LISTEN`/`NOTIFY`** | ❌ **정정됨 (2026-08-05 재실측, 아래 6번)** — 대화형 psql로 측정해 잘못된 결론을 냈다. **유휴 세션에는 알림이 전달되지 않는다** (§7-3). ~~폴링 주기를 5→30초로 늘릴 수 있다~~ → **철회** |
| 4' | HNSW 인덱스 생성 실행 | ✅ 성공 |
| 10 | `max_connections` / `pool_size` | ✅ `max_connections=100`, OpenProxy `pool_size=10` |
| 13 | `pg_trgm` 설치 | ✅ 성공 — ADR-016의 한국어 부분 일치 대안 확보 |
| 5 | OpenProxy 실제 설정 | ✅ 확인 — `pool_mode="session"`, `query_parser_enabled=false` (§5-1) |

### ✅ M1 이후 측정 완료 (2026-08-05, 이슈 #16)

측정 DB는 저장소의 `backend/migrations/001~004`를 **마이그레이션 러너로 실 OpenSQL에 그대로 적용**해
만들었다(4개 파일 전부 1회에 성공 — 실 클러스터 첫 적용). 문서 500건 / 청크 6000행 / 1024차원.

| # | 검증 항목 | 결과 |
|---|---|---|
| 6 | LISTEN 연결의 `server_lifetime`/`idle_timeout` 실동작 | ⚠️ **더 큰 문제를 발견** — 유휴 세션은 70분간 멀쩡했으나(pid 불변), 애초에 **OpenProxy 경유로는 비동기 알림이 전달되지 않는다**(§7-3). 1번 결론이 정정됐고 폴링 주기 상향은 철회됐다 |
| 12 | **`avg` 결과가 HNSW 인덱스를 타는지** | ✅ **탄다** — `InitPlan`으로 접혀 프로브로 정상 동작한다. 대비하던 "왕복 2회"는 필요 없다. ~~인덱스를 막는 것은 벡터 정렬 서브쿼리 안의 `documents` JOIN이다~~ → **17번에서 반증됨. JOIN은 막지 않는다** |
| 14 | `hnsw.ef_search`의 유효 범위 | 🆕 **양쪽에 벽이 있다.** `LIMIT`보다 작으면 **에러 없이 과소 검색**(기본 40에서 `LIMIT 50` → 40행), 너무 크면 플래너가 인덱스를 버린다(600에서 7배 느려짐). ADR-011의 `200`은 이 창 안에 있다 → **22번에서 보강: 위쪽 벽 600은 `rpc=4` 체제·합성 벡터 값이다. `rpc=1.1`·실 BGE-M3에서는 200~400 사이이며, `200`의 근거는 22번을 쓴다** |
| 15 | OpenProxy의 세션 상태 초기화 범위 | 🆕 `RESET ALL`은 하되 **`DISCARD ALL`은 하지 않는다** — LISTEN 등록·임시 테이블이 다음 클라이언트로 누수된다 (§5-2) |

**12번 상세 — 쿼리 형태별 HNSW 사용 여부** (`enable_seqscan=off`로 능력 검증)

> ⚠️ **아래 표의 마지막 두 행은 17번 재측정에서 반증됐다.** 원문을 남기되 결론은 17번을 따른다.

| 형태 | HNSW | 시간 |
|---|---|---|
| 상수 리터럴 프로브 | ✅ | 45ms |
| `avg` 서브쿼리, JOIN 없음 | ✅ | 39ms |
| `avg` + `document_chunks` 자체 필터 | ✅ | 41ms |
| ~~**`cand` CTE 안에 `documents` JOIN + 권한 필터 (구 설계)**~~ | ~~**❌ 강제해도 불가**~~ | ~~255ms~~ |
| ~~후보 확보 후 밖에서 JOIN·권한 필터 (신 설계)~~ | ~~✅~~ | ~~106ms~~ |

**14번 상세** (6000행, `LIMIT 50` 고정, 강제 없음)

| `ef_search` | 스캔 | 시간 | 반환행 |
|---|---|---|---|
| 40 (기본) | HNSW | 74ms | **40** ← 조용한 과소 검색 |
| 60 / 100 / 200 / 400 | HNSW | 83 / 99 / 124 / 168ms | 50 |
| 600 | **Seq Scan** | **1177ms** | 50 |

> **재현 시 함정 셋** — ① `CROSS JOIN LATERAL`의 난수 서브쿼리는 접힌다(2000행에 고유 벡터 20개만
> 생성됐다). 차원을 `generate_series`로 펼친 뒤 `array_agg ... GROUP BY`로 모아야 행마다 난수가 된다.
> ② 한 문장으로 INSERT한 행들은 `created_at`이 전부 같아 `ORDER BY created_at OFFSET n`이 무의미하다.
> ③ `EXPLAIN` 출력에 1024차원 리터럴이 그대로 들어가 38KB를 넘는다.

### ✅ 재측정 완료 (2026-08-05, 실 BGE-M3 임베딩)

**측정 조건이 12·14번과 다르다.** 12·14번은 합성 벡터였고, 아래는 실제 `BAAI/bge-m3` 임베딩이다.
워크스페이스 실제 텍스트 1009개 파일에서 420자씩 **겹침 없이** 6000청크를 뽑아
`normalize_embeddings=True`로 임베딩했다. **고유 벡터 6000/6000**, 코사인 유사도 평균 0.498 ·
중앙 0.490 · 최대 0.991. HNSW 47MB · 힙 3072kB.

| # | 검증 항목 | 결과 |
|---|---|---|
| 16 | 플래너가 **강제 없이** HNSW를 고르는 조건 | ❗ **규모가 아니라 `random_page_cost`였다.** 기본값 4에서는 어떤 쿼리 형태도 HNSW를 쓰지 않는다(624~785ms). **1.1로 낮추면 쿼리를 그대로 두고 33~36ms** — 18~22배 (ADR-011 보강 5) |
| 17 | **JOIN이 원인이라는 결론의 변수 분리** | ❌ **12번 결론이 반증됐다.** JOIN은 HNSW를 막지 않는다. 네 조건 전부 인덱스를 쓴다 (ADR-018 재개정) |

**17번 상세 — 변수 분리** (`enable_seqscan=off`, `ef_search=200`, `LIMIT 100`)

| 조건 | HNSW | 시간 |
|---|---|---|
| A. JOIN 없음 · 필터 없음 | ✅ | 29.0ms |
| B. JOIN 있음 · 필터 없음 *(1차 측정에 없던 조건)* | ✅ | 43.9ms |
| **C. JOIN 있음 · 권한 필터 (구 설계)** | **✅** | **32.7ms** |
| D. 후보 확보 후 밖에서 JOIN (신 설계) | ✅ | 34.4ms |

C가 1차 측정에서 "❌ 강제해도 불가 · 255ms"였다. **두 측정 모두 실 VM·6000행이었고 차이의 원인은
규명하지 못했다.** 난수 벡터로도 돌려봤으나 같은 결론(A~D 전부 HNSW, 35~39ms)이 나왔다 —
플래너의 인덱스 선택 비용 추정은 벡터 값에 의존하지 않으므로 예상된 결과다.

**16번 상세 — `random_page_cost`** (강제 없음, `LIMIT 100`)

| 쿼리 | `rpc = 4` (VM 기본) | `rpc = 1.1` |
|---|---|---|
| 관련 문서 — 필터를 `cand` 안에 | Seq Scan **624ms** | **HNSW 33.8ms** |
| 관련 문서 — 필터를 밖으로 | Seq Scan **785ms** | **HNSW 35.5ms** |
| 관련 문서 — 밖으로 · `LIMIT 300` | Seq Scan 761ms | — |
| 하이브리드 검색 (태그 필터) | Seq Scan 234ms | Seq Scan 232ms |

힙 3MB에 HNSW 인덱스 47MB이고 그래프 탐색은 전부 임의 접근이다. `rpc=4`는 회전 디스크 기본값이라
"통째로 읽는 편이 싸다"가 나온다. 태그 필터가 붙은 검색만 `rpc`와 무관하게 Seq Scan인데, 필터가
선택적(500문서 중 84개)이라 먼저 좁히는 편이 실제로 싸다 — 플래너가 옳다.

**부수 측정 — 필터를 밖으로 빼면 후보가 낭비된다.** 후보 100개가 퍼지는 문서 수(문서 20건 표본,
비공개 20%): 필터 안 **54.5개**(최소 30) vs 필터 밖 **40.5개**(최소 18). `k=10`에는 둘 다 여유가
있으나 얻는 것 없는 손실이라 ADR-018 개정을 철회했다.

> ⚠️ **17번 재현 시 — 적재 벡터의 퇴화를 반드시 확인한다.** `count(DISTINCT embedding::text)`가
> 행 수와 같아야 한다. 상관관계 없는 `LATERAL`/서브쿼리는 한 번만 평가되어 **에러 없이** 전 행이
> 같은 벡터가 된다. 재측정에서 두 번 밟았다.
>
> | 상태 | 고유 벡터 | HNSW 크기 | 6000행 삽입 |
> |---|---|---|---|
> | 전 행 동일 | 1 | 6MB | 43초 |
> | `chunk_index`에만 상관 | 12 | — | — |
> | 문서·청크 양쪽 상관 (정상) | 6000 | 47MB | 8분 |
>
> **퇴화 상태에서는 인덱스 크기와 삽입 시간이 한 자릿수 배 달라진다.** 대량 적재는 인덱스를 지우고
> `COPY` 후 재생성하는 편이 빠르다 (COPY 13초 + 인덱스 42초).

### ✅ M3 이후 측정 완료 (2026-08-06, 실 OpenSQL에서 애플리케이션 완주)

지금까지의 실측은 psql·스크립트로 **개별 쿼리**를 잰 것이었다. 아래는 **맥의 FastAPI·워커·Next.js가
OpenProxy(6432)에 붙어 업로드부터 검색까지 완주하는지**를 본 것으로, 애플리케이션 전체가 실 OpenSQL
위에서 도는 것을 처음 확인했다. 임베딩도 `FakeProvider`가 아니라 실제 `BAAI/bge-m3`다.

| # | 검증 항목 | 결과 |
|---|---|---|
| 19 | **파이프라인 완주** (업로드 → 트리거 → 워커 → `ready` → 검색) | ✅ 브라우저 E2E 3항목 통과 (아래 상세) |
| 20 | **풀이 바라보는 데이터베이스** | ❗ 설치기 기본값이 `database = "postgres"`라 애플리케이션 데이터가 관리용 기본 DB에 쌓이고 있었다. `"opensql"`로 교정 (§5-1) |

**19번 상세 — 브라우저 E2E** (Playwright · `next start` 프로덕션 빌드 · 데모 사용자 alice)

| 시나리오 | 결과 |
|---|---|
| 업로드 → 상태 배지 | `대기 중` → `완료` **21.2초** (BGE-M3 최초 로딩 포함) |
| 편집 저장 → 정합성 카운터 | 워커 정지 상태로 저장 → `/admin/status` 카운터 **1**, 워커 재개 **3.5초** 후 **0** |
| 태그 없이 검색 → 상세 이동 | 1건(유사도 0.571) → 클릭 → 문서 상세 진입 |
| 브라우저 콘솔 | 에러 **0건** |

완주 후 DB 상태: `version=v2`, `embedding_status=ready`, 청크 1개(**v2 기준**), 텍스트 버전 이력 2건
(v1은 INSERT 트리거, v2는 편집), 벡터 **1024차원**, 잡 2건 모두 `done`, 정합성 어긋난 문서 **0**.
애플리케이션이 `document_versions`·`embedding_jobs`를 직접 건드리지 않고 **트리거만으로 채워지는 것이
실 OpenSQL에서 확인됐다.**

앞서 같은 경로로 업로드 → 삭제를 돌려 `ON DELETE CASCADE`가 잡·버전 이력을 원자적으로 지우는 것도
확인했다(문서 501 → 500, 잡·이력 0건).

> **정합성 카운터를 눈으로 보려면 워커를 잠깐 멈춰야 한다.** 워커가 돌고 있으면 어긋난 구간이 1초 미만
> 이라 2초 폴링 화면으로는 잡히지 않는다(위 3.5초는 재개 후 폴링 틱까지 포함한 값). 데모에서도 워커를
> 멈췄다 켜는 편이 1 → 0 전이를 확실히 보여준다.

> **측정 DB는 `opensql`이고, 16·17번의 6000청크 벤치마크는 `postgres` DB에 그대로 있다.** 20번 교정으로
> 풀이 `opensql`을 바라보므로, 벤치마크를 다시 쓰려면 OpenProxy를 거치지 않고
> `psql -h <VM_IP> -p 5432 -U postgres -d postgres`로 직결한다. 그 코퍼스는 적재 스크립트가 저장소에
> 없어 **동일하게 재생성할 수 없으므로 지우지 않는다.**

### ✅ M4 쿼리 측정 완료 (2026-08-06, 관련 문서·태그 추천)

16·17번과 **같은 코퍼스**에서 M4의 `RELATED_SQL` 전문을 쟀다 — `postgres` DB의 실 BGE-M3 6000청크,
문서 500건(문서당 12청크), public 400 / private 100, owner 10명. OpenProxy는 이 DB로 라우팅하지
않으므로 5432 직결이다. 측정 전 `count(DISTINCT embedding::text) = 6000`으로 퇴화가 없음을 확인했다.

| # | 검증 항목 | 결과 |
|---|---|---|
| 21 | 관련 문서 쿼리의 HNSW 사용 여부 | ✅ **`avg`를 CTE에 둔 채로 탄다.** `rpc=4` Seq Scan 632~699ms → `rpc=1.1` HNSW 33~46ms (약 19배). 이슈 #9가 대비한 "못 타면 평균을 별도 조회해 파라미터로 넘긴다"는 불필요하다 |
| 22 | `rpc=1.1` 체제의 `ef_search` 상한 | 🆕 **위쪽 벽은 200~400 사이다.** 400부터 플래너가 인덱스를 버린다. 14번의 벽(600)은 `rpc=4` 체제 측정이라 그대로 쓸 수 없었다 |

**21번 상세** (`RELATED_SQL` 전문 · `k=10` → 후보 `LIMIT 100` · `ef_search=200` · 3회)

| `random_page_cost` | 스캔 | 시간 |
|---|---|---|
| 4 (VM 기본) | `Seq Scan on document_chunks` | 699 / 633 / 632 ms |
| **1.1** | **`Index Scan using idx_chunks_embedding`** | **46 / 39 / 33 ms** |

두 계획 모두 `best` CTE에서 40행을 내므로 결과는 같고 경로만 다르다. 16번이 검색 쿼리에서 얻은
결론(624~785ms → 33~36ms)이 관련 문서 쿼리에서도 재현됐다.

**22번 상세 — `ef_search` 임계** (`rpc=1.1` 고정 · 후보 `LIMIT 100` 요청)

| `ef_search` | 실제 후보 | 스캔 | 시간 |
|---|---|---|---|
| 미설정(기본 40) | **21행** | HNSW | 13.6ms |
| 40 | **21행** | HNSW | 10.7ms |
| 100 | **60행** | HNSW | 19.8ms |
| **200** | **100행** ✓ | HNSW | 29.1ms |
| 400 | 100행 | **Seq Scan** | 619.4ms |
| 600 | 100행 | **Seq Scan** | 620.5ms |
| 1000 | 100행 | **Seq Scan** | 595.8ms |

**아래로는 에러 없이 행이 모자라고, 위로는 인덱스를 버린다.** 기본값 40에서는 요청한 100개 중 21개만 온다.

**등호에서도 모자란다** — 별도 측정에서 `ef_search=200`에 `LIMIT 200`을 요청하니 **193행**이 왔다.
M4 코드가 배수를 상수로 박지 않고 예산에서 역산하는 이유다
(`CANDIDATE_MULTIPLIER = (EF_SEARCH - 1) // MAX_K`, `app/services/related.py`).

> **14번과 16번의 모순이 정리됐다.** 14번은 "기본 `rpc`에서도 HNSW를 쓴다(74~168ms)"였고 16번은
> "`rpc=4`에서는 어떤 형태도 쓰지 않는다(624~785ms)"였다. 이번 측정은 **16번을 지지한다.** 두 측정의
> 차이는 벡터 조건(14번은 합성, 16번·이번은 실 BGE-M3)과 `LIMIT`(50 vs 100)이며, 14번 상세의 함정 ①이
> 기록하듯 당시 합성 벡터에 **퇴화가 있었을 가능성**이 있다 — 퇴화하면 인덱스가 작아져 플래너가 고르기
> 쉬워진다. 원인을 확정하지는 못했으나, **`ef_search` 상한의 근거로는 같은 코퍼스에서 잰 22번을 쓴다.**

**태그 추천 쿼리(`TAG_SUGGESTION_SQL`)는 별도로 측정하지 않았다.** 후보 `LIMIT`이 같은 형태이고
`ef_search` 아래에 있으므로 같은 결론이 적용된다고 보았다.

### ✅ #27 장애 주입 측정 완료 (2026-08-09)

| 시나리오 | 결과 |
|---|---|
| PostgreSQL postmaster `SIGKILL` | Patroni가 46 ms에 감지하고 5.85초에 접속을 복구했다. 리더·timeline은 바뀌지 않았다 |
| etcd 99초 정지 | `failsafe_mode`가 primary를 유지해 6432·5432 쓰기가 전 구간 가능했고, 재기동 2.6초 뒤 DCS 상태가 복원됐다 |
| Patroni `SIGKILL` | PostgreSQL 쓰기는 계속 가능했지만 106초 동안 Patroni를 되살릴 주체가 없었다. 수동 기동 후 기존 postmaster를 인수했다 |

이 측정은 **DB 프로세스 장애 자동 복구**와 DCS 장애 내성을 확인한 것이지, replica 승격을 수반하는
failover를 시연한 것이 아니다. 상세 조건과 타임라인은 §0 "Single 장애 주입 실측"에 있다.

### 🔴 아직 남은 실측

| # | 검증 항목 | 방법 | 실패 시 영향 |
|---|---|---|---|
| 18 | `rpc=4`에서도 플래너가 HNSW를 고르기 시작하는 **규모** | 청크를 수만~수십만 행으로 늘려 `EXPLAIN` | 순차 스캔 비용은 행 수에 선형, HNSW는 거의 로그이므로 어느 지점부터는 기본값에서도 인덱스를 고른다. `rpc=1.1`은 그 지점을 앞당기는 것이며 데모 규모에서 필요한 이유가 그것이다. **교차점을 모른다고 해서 지금 결정이 바뀌지는 않는다** — 근거 보강 성격이다 |

### ⛔ Single 구성에서 검증 불가능한 항목

| # | 항목 | 사유 |
|---|---|---|
| 7 | 리더 선출·승격·`timeline` 증가 | 승격 대상 replica가 없고 `patronictl history`도 `[]`다 |
| 8 | OpenProxy의 새 프라이머리 자동 발견 | 기능 검증 이전에 `use_patroni`·`[general.etcd]` 설정 자체가 없다 |
| 23 | watchdog 펜싱 | `/dev/watchdog` 권한이 없어 Patroni watchdog이 비활성이다 |
| 24 | VIP failover | Single 구성에는 이중화된 OpenProxy와 VRRP VIP가 없다 |

사무국이 Single 구성을 지시했으므로(§0) 이 네 항목은 현재 구성에서 검증할 수 없다. 반면
PostgreSQL 프로세스 장애 자동 복구와 etcd 장애 중 primary 유지는 #27에서 실측했다. `patroni.yml`의
파라미터와 `finalize_single_to_ha.sh`는 HA 전환 경로가 제품에 남아 있다는 근거일 뿐, 사무국의
Single 지시를 어길 근거는 아니다. 고가용성 요건의 결정 정정은 후속 step에서 ADR-020에 기록한다.

> **11·12번 배경**: 관련 문서·태그 추천(ADR-018·019)이 문서 대표 벡터를 저장하지 않고 **질의 시점 `avg(embedding)`**으로 구한다. 저장 컬럼을 만들지 않아 동기화 대상이 늘지 않는 대신, 플래너가 `(SELECT avg(...) FROM ...)`을 상수로 접지 못하면 HNSW 인덱스 정렬을 활용하지 못하고 풀스캔이 된다. **기능 가부(11)는 ✅ 확인됐고, 성능(12)이 남았다.**
>
> **13번 배경**: `pg_trgm`은 §1의 번들 확장 목록에 **없다.** PostgreSQL contrib 모듈이라 설치본에 포함될 가능성이 높다고 봤는데, **실측 결과 설치된다.**

### 실측 재현 명령

라이선스 만료 전에 다시 확인해야 할 일이 생기면 아래를 쓴다. VM에서 `sudo su - opensql` 후 실행한다.

```bash
# 버전 (소켓 접속은 trust라 비밀번호 불필요)
psql -U postgres -c "SELECT version();"
psql -U postgres -c "SELECT name, default_version FROM pg_available_extensions WHERE name LIKE '%vector%';"
psql -U postgres -c "SHOW max_connections;"

# HNSW + avg
psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -U postgres -c "CREATE TABLE _t (id int, v vector(1024));"
psql -U postgres -c "CREATE INDEX ON _t USING hnsw (v vector_cosine_ops);"
psql -U postgres -c "SELECT avg(v) FROM (SELECT '[1,2,3]'::vector AS v) s;"
psql -U postgres -c "DROP TABLE _t;"

# pg_trgm
psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 클러스터 상태
patronictl -c $OPENSQL_HOME/etc/patroni/patroni.yml list
```

**LISTEN/NOTIFY는 터미널 2개가 필요하다.**

```bash
# 터미널 1 — OpenProxy 경유 (-d 는 pool 이름)
PGPASSWORD=pg_password psql -h <VM_IP> -p 6432 -U postgres -d opensql
opensql=> LISTEN ch1;
opensql=> SELECT 1;          -- 알림은 다음 쿼리 실행 시 표시된다

# 터미널 2 — 다른 세션에서 발행
psql -U postgres -c "NOTIFY ch1, 'hello from another session';"
```

> ⚠️ **이 절차로는 "동작한다"만 알 수 있고, 그 결론은 틀렸다 (§7-3).** 위 `SELECT 1`이 바로
> OpenProxy가 쥐고 있던 알림을 밀어내는 트리거다. **유휴 클라이언트를 재현하지 못하므로
> 워커가 깨어나는지는 psql로 확인할 수 없다.** 비동기 전달을 보려면 아무 쿼리도 보내지 않는
> 클라이언트가 필요하다 — 노드 직결(5432)과 OpenProxy(6432)를 같은 스크립트로 비교할 것.

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

## 14. 관계 판정 신호 실측 [실측 2026-08-11]

### 측정 환경과 재현 방법

Step 1의 `scripts/seed_demo.py`가 저장소 문서를 복사해 넣은 로컬
`pgvector/pgvector:pg17` 컨테이너에서 측정했다. OpenSQL VM의 0.8.1이 아니라 로컬 pgvector
0.8.6 환경이다. 데이터는 문서 **60건**(private 4건), 청크 **262행**이고,
`count(DISTINCT embedding::text) = 262`로 퇴화가 없었다. 문서당 청크 수는 최소 1, 중앙 3,
최대 29다.

재현 스크립트는 `scripts/measure_relations.py`다. 모든 벡터 측정은 명시적 트랜잭션 안에서 다음
두 설정을 함께 적용했다.

```sql
SET LOCAL hnsw.ef_search = 200;
SET LOCAL random_page_cost = 1.1;
```

### `NEIGHBOR_N` — 지정된 상관 LATERAL은 HNSW를 타지 않는다

청크마다 다른 문서의 이웃 청크 N개를 구하는 step 정의의 쿼리를
`EXPLAIN (ANALYZE, BUFFERS)`로 측정했다. 단일 문서는 청크 수가 중앙값에 가까운
`1. 구성요소`(4청크)를 사용했다. `다른 문서/청크`는 각 청크의 N개 이웃이 평균 몇 개의 서로
다른 문서에 퍼졌는지이며, 뒤의 두 값은 최소·최대다.

| N | 전체 262청크 | 단일 문서 4청크 | 다른 문서/청크 | HNSW |
|---:|---:|---:|---:|---|
| 5 | 180.9 ms | 2.91 ms | 3.69 (1~5) | ❌ |
| **10** | **176.1 ms** | **2.92 ms** | **6.35 (2~10)** | ❌ |
| 20 | 177.4 ms | 3.02 ms | 10.72 (5~19) | ❌ |
| 40 | 173.6 ms | 3.17 ms | 17.87 (11~28) | ❌ |

계획에는 `Index Scan using idx_chunks_embedding`이 한 번도 나오지 않았다. 안쪽 후보 조회는 매
외부 청크마다 `Seq Scan on document_chunks` → `top-N heapsort`였고, `enable_seqscan=off`를
추가해도 HNSW가 아니라 문서 ID B-tree를 읽고 정렬했다. 원인은 비용 임계가 아니라
`ORDER BY c.embedding <=> me.embedding`의 **외부 행 벡터로 HNSW 정렬 스캔을 파라미터화하지
못하는 쿼리 형태**다. 따라서 `rpc=1.1` 없이 재서 생긴 결과가 아니다.

이 결과는 현재 262청크의 절대 시간만 보여준다. 전체 비용이 N과 거의 무관한 것도 매번 전체를
읽기 때문이다. **상관 LATERAL 전체 계획의 수치는 규모 확장 근거로는 무효**이며, step 6이 이
형태를 그대로 트리거에 넣어도 된다는 근거로 쓰지 않는다. step 3에서 청크별 상수 프로브 또는
비동기 계산으로 쿼리 형태를 바꿀지 재검토해야 한다.

그럼에도 순위 컷오프 자체는 **`NEIGHBOR_N = 10`**으로 정한다. 5는 한 청크의 이웃이 평균 3.69개
문서에만 퍼졌고, 10은 6.35개로 늘어난다. 20·40은 edge 후보와 쓰기량을 각각 두 배·네 배로
늘리는 데 비해 이 데이터에서 필요한 실제 문서쌍 판별은 10에서 이미 잡혔다. `10 < EF_SEARCH
(200)`으로 등호 벽에서도 충분히 멀다. 절대 거리 임계는 도입하지 않았다.

### `OVERLAP_RATIO` — 0.8

`NEIGHBOR_N=10`에서, 내 청크 중 상대 문서를 이웃으로 한 청크 수를 내 전체 청크 수로 나눴다.
0.1 폭 히스토그램은 다음과 같다.

| 비율 | 쌍 수 | 비율 | 쌍 수 |
|---|---:|---|---:|
| 0.0~0.1 | 48 | 0.5~0.6 | 17 |
| 0.1~0.2 | 100 | 0.6~0.7 | 37 |
| 0.2~0.3 | 77 | 0.7~0.8 | 27 |
| 0.3~0.4 | 78 | 0.8~0.9 | 11 |
| 0.4~0.5 | 130 | 0.9~1.0 | 215 |

1청크 문서는 한 번만 잡혀도 비율이 1이므로 마지막 버킷 215쌍을 그대로 "전반 동일"의 증거로
읽을 수는 없다. 긴 문서의 정답쌍을 따로 확인했다.

| 방향 | 매칭 | 비율 |
|---|---:|---:|
| `검색 데이터 흐름` → `ADR-011` | 10/10 | 1.000 |
| `ADR-011` → `검색 데이터 흐름` | 9/9 | 1.000 |
| `관련 문서·태그 추천` → `ADR-018` | 8/9 | 0.889 |
| `ADR-018` → `관련 문서·태그 추천` | 5/6 | 0.833 |
| `CLAUDE.md` → `ADR-011` | 2/10 | 0.200 |
| `ADR-011` → `CLAUDE.md` | 8/9 | 0.889 |

서로 같은 설계를 반복한 ADR↔Architecture 쌍은 양방향 0.833 이상이고, 여러 규칙을 포괄하는
CLAUDE.md에서 개별 ADR로 향하는 방향은 0.200이다. 0.8 아래 버킷과 0.8 이상 구간이 이를
가르므로 **`OVERLAP_RATIO = 0.8`**로 정한다. 분포가 전 구간에 균일해 두 kind의 근거가
사라지는 설계 변경점 (b)는 걸리지 않았다. 다만 짧은 문서 편향은 step 3 ADR에 한계로 남긴다.

### `BROADER_MARGIN` — 판정 실패, broader를 m7에서 제거

`pg_trgm`을 측정용으로 설치하고 `word_similarity` 양방향 차를 확인했다.

| 쌍 | A→B | B→A | 절대 차 |
|---|---:|---:|---:|
| `CLAUDE.md` ↔ `ADR-011` | 0.237 | 0.254 | 0.017 |
| `CLAUDE.md` ↔ `ADR-018` | 0.243 | 0.267 | 0.023 |
| `ADR-011` ↔ `검색 데이터 흐름` | 0.302 | 0.297 | 0.005 |
| `ADR-018` ↔ `관련 문서·태그 추천` | 0.472 | 0.481 | 0.010 |
| 무관: `ADR-011` ↔ `SETUP_OPENSQL` | 0.173 | 0.162 | 0.011 |

정답을 아는 포괄↔상세 쌍도 무관한 쌍과 차가 겹친다. 전체 1,770쌍 중 **1,725쌍(97.5%)**이
차 0.1 미만이었고, 절대 차의 p50/p75/p90/p95는 각각 0.0167/0.0369/0.0597/0.0781이었다.
한국어 조사만의 국소 문제가 아니라 긴 Markdown 문서의 공통 어휘가 양방향 최고 부분 일치를
비슷하게 만드는 현상이다.

따라서 진단 기준값은 **`BROADER_MARGIN = 0.10`**으로 확정하되, 이 값으로 방향을 인정하지
않는다. 마진 미달 97.5%이며 정답쌍도 전부 미달이므로 설계 변경점 (c)가 걸렸다. **m7에서는
`broader`를 빼고 해당 후보를 `related`로 폴백한다.** 관계 kind는 계획했던 3종이 아니라
`overlaps`·`related` 2종이 된다. step 3의 ADR이 이 변경을 정식 결정으로 기록해야 한다.

### 확정값과 설계 변경점 판정

| 상수 | 값 | 적용 |
|---|---:|---|
| `NEIGHBOR_N` | **10** | 청크당 순위 기반 이웃 수. `10 < EF_SEARCH(200)` |
| `OVERLAP_RATIO` | **0.8** | 이상이고 아래 하한을 함께 넘겨야 `overlaps`, 아니면 `related` |
| `MIN_MATCHED_CHUNKS` | **2** | 매칭 청크 절대 수 하한 **[2026-08-11 추가]** |
| `BROADER_MARGIN` | **0.10** | 진단값만 보존. 실제 broader 판정은 사용하지 않음 |

#### `MIN_MATCHED_CHUNKS` — 2 **[추가 2026-08-11]**

위 히스토그램 문단이 *"1청크 문서는 한 번만 잡혀도 비율이 1"*이라고 적어두고도 **상수에는 그
장치를 넣지 않았다.** m7 step 9의 화면 검증에서 그 대가가 드러났다.

| 청크 수 | 문서 수 | 문서당 평균 `overlaps` |
|---:|---:|---:|
| 1 | 19 | 9.6 |
| 2 | 10 | 7.9 |
| 3 | 8 | 7.7 |

이웃은 청크당 10개이므로 **1청크 문서는 사실상 이웃 전부와 `overlaps`가 된다.** 전체 502건의
60%가 청크 3개 이하 문서에서 나왔다. 재현: 요리 레시피 한 편(1청크)을 올리면 `OpenSQL 개발
환경 구축`·`고가용성(HA) 전략`과 비율 **1.000**으로 붙는다.

비율만으로는 막을 수 없다 — 분모가 자기 청크 수라 짧은 문서일수록 임계가 무력해진다. 매칭
청크의 **절대 수 하한 2**를 함께 요구해 *"두 대목 이상에서 만난다"*를 성립 조건으로 둔다.
거리 임계가 아니라 개수인 이유는 `NEIGHBOR_N`과 같다 — 개수는 임베딩 거리 분포에 의존하지
않아 심사위원의 문서에서도 같은 뜻을 유지한다. 청크 2개 문서는 둘 다 겹쳐야 하므로 통과할 수
있고, 1청크 문서는 구조적으로 `related`가 된다.

- **(a) 1초 문턱은 걸리지 않았다** — 대표 문서 2.92 ms, 최대 29청크 문서를 단순 비례해도 현재
  데이터에서는 1초 아래다. 그러나 HNSW를 전혀 못 타는 더 근본적인 문제가 드러났으므로 step
  3에서 트리거 내부 상관 LATERAL 유지 여부를 재검토한다.
- **(b) 걸리지 않았다** — 알려진 반복 설계 쌍과 포함 방향이 0.8 경계에서 갈렸다.
- **(c) 걸렸다** — 정답쌍에서도 trigram 방향 차가 벌어지지 않아 broader를 제거한다.

### 이 step에서 잴 수 없는 항목

| 미측정 | 이유 | 넘길 자리 |
|---|---|---|
| `WITH RECURSIVE`에서 HNSW·인덱스를 타는가 | `document_edges`가 아직 없음 | **step 8** |
| 권한 필터를 재귀항에 넣었을 때의 계획 변화 | 같은 이유 | **step 8** |
| 조회 시점 제목 resolve 조인 비용 | 위키링크가 m7 범위 밖 | **m9** |

### Step 8 — 재귀 순회와 권한 필터 계획

Step 6이 만든 seed 상태(문서 62건, 청크 274행, `document_edges` 1,462행)에서
`WITH RECURSIVE` 깊이 2 순회를 `EXPLAIN (ANALYZE, BUFFERS)`로 측정했다. 두 형태 모두 plain
트랜잭션 안에서 `hnsw.ef_search=200`, `random_page_cost=1.1`을 적용했고 같은 공개 문서를
진입점으로 사용했다.

| 권한 필터 위치 | 실행 시간 | 재귀 결과 | shared hit | edge 접근 |
|---|---:|---:|---:|---|
| **재귀항 안** | **2.65 ms** | 926행 | 2,042 | Index Only Scan |
| 재귀 바깥 | 1.82 ms | 1,030행 생성 후 972행 노출 | 99 | Index Only Scan |

재귀항은 edge를 순차 스캔하지 않았다. `src_document_id`로 인덱스 프로브했지만 플래너는
`idx_document_edges_src_kind` 대신 같은 선두 키를 가지면서 목적지와 kind까지 덮는
`uq_document_edges_relation`을 골랐다. 전용 `(src_document_id, kind)` 인덱스는 진입점의
`EXISTS`에서 실제 사용됐다. 즉 `random_page_cost=1.1`이 적용된 계획에서 재귀 edge 조회는
인덱스를 타며, 현재 seed에서 깊이 2는 3 ms 미만이라 **깊이를 2로 유지한다.**

외부 필터 형태가 작은 seed에서는 0.83 ms 빨랐지만 private 노드까지 순회해 재귀 결과를
104행 더 만든 뒤 마지막에 버렸다. 이 형태는 private 노드를 경유해 그 너머 공개 문서에 닿을
수 있으므로 성능과 무관하게 ADR-027을 위반한다. 구현은 비용을 감수하고 권한 조인을 재귀항
안에 둔다. 이 측정은 62문서의 계획 검증이며 대규모 그래프의 지연 근거로 확대 해석하지 않는다.
