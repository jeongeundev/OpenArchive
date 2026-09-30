# OpenSQL 공식 구성과 다른 점

이 저장소의 HA 환경(node1~3, `SETUP_OPENSQL.md` §16)이 **OpenSQL 공식 구성과 어디서, 왜 다른지**를 한곳에 모은다.
원칙은 **공식 구성이 기본이고, 다르게 하려면 이유가 실측으로 있어야 한다**이다(#150). 이 원칙이 없던 동안
"실무 표준"이라는 일반론으로 동기 복제를 켰다가 제품 결함을 밟았다(#148, ADR-049 개정).

## 무엇을 "공식"으로 보는가

| 출처 | 내용 | 위치 |
|---|---|---|
| ① OpenSQL 문서 | Tmax의 설치·운영 매뉴얼 (docs.tibero.com/tmaxopensql) | 사본 `notes/ha110/docs/` (2026-09-25 수집, 40쪽) |
| ② OpenSQL 배포판 | 설치기와 템플릿 — `opensql-installer/config/{patroni.yml, openproxy.toml, *.service, *.env}`, `openproxy/openproxy.ha.template.toml` | `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720` |

- **둘이 갈리면 HA에 대해 명시적으로 말하는 쪽을 따른다.** 설치기는 "일단 도는" 값을 넣고(OpenProxy `session`·파서 끔·
  슈퍼유저 풀), HA 운영 방법은 문서와 HA 템플릿이 따로 적는다("etcd 연동 — HA 환경 필수"). 설치기 기본 OpenProxy로는
  VIP 경유 쓰기 10건 중 6~8건이 실패했다(`SETUP_OPENSQL.md` §16).
- **PostgreSQL 파라미터 중 OpenSQL이 정하지 않은 것은 PostgreSQL 기본값이 곧 공식값이다.** 설치기 `patroni.yml`은
  `max_connections`·`wal_level` 등 일부만 정한다. 나머지를 바꾸는 것은 "공식에 없는 것을 더했다"로 센다.

## 1. 문서·배포판 둘 다와 다른 것 — 우리가 더한 것

| 설정 | 값 | 왜 | 근거 | 되돌리면 |
|---|---|---|---|---|
| PostgreSQL 서버 keepalive — `tcp_keepalives_idle`·`_interval`·`_count`, `tcp_user_timeout` (Patroni DCS) | 30 · 10 · 3 · 60000ms (기본 OS 7200초 · 재전송 약 15분) | OpenProxy **노드가 통째로 죽으면** 그 노드를 거치던 트랜잭션이 Primary에 락을 쥔 채 남는다. 전원이 끊긴 상대는 FIN도 RST도 보내지 않는다. 문서의 keepalive는 **OpenProxy 쪽** 설정이라 OpenProxy가 죽으면 쓸 수 없다 — OpenSQL 안에 대안이 없다 | ADR-051. #122 S5a-2에서 워커 13분 정지(`embedding_jobs` 행 락 761초 대기) | 죽은 OpenProxy 노드의 고아 트랜잭션이 최대 약 2시간 락을 쥔다 |
| OpenProxy 기동 전 로컬 etcd 응답 대기 — systemd 드롭인 `ExecStartPre` (최대 30초) | `etcdctl endpoint health`가 될 때까지 | `After=etcd.service`만으로는 부족하다. etcd 유닛이 `Type=simple`이라 "Started"가 포트 준비를 뜻하지 않고, OpenProxy는 안 열린 etcd에 **한 번** 시도한 뒤 로컬 파일로 대체해 뜬다. 대체된 노드는 이후 공유 설정 변경을 놓친다 | 재부팅 실측 2회(`SETUP_OPENSQL.md` §16 교정 4). `After=network.target etcd.service` 자체는 문서(OpenProxy HA 페이지) 예시에 있다 | 재부팅한 노드가 옛 로컬 설정으로 서비스할 수 있다 |

## 2. 배포판(설치기 출력)과는 다르지만 문서·HA 템플릿대로인 것

| 설정 | 설치기 출력 | 현재 (= 문서·HA 템플릿) | 근거 |
|---|---|---|---|
| OpenProxy 풀 모드·라우팅 | `session` · 파서 끔 · `default_role=primary` | `[general.etcd]` · `transaction` · `query_parser_read_write_splitting` · 서버 `Auto` · `use_patroni` | HA 템플릿 "etcd 연동 (HA 환경 필수)". 설치기 값으로는 쓰기 10건 중 6~8건 "읽기 전용" 실패 (§16 교정 3) |
| 앱 접속 계정 | 풀 사용자가 `postgres` 슈퍼유저 하나 | 앱 전용 롤·DB·풀 `openarchive`(비슈퍼유저, `server_username` 명시). `postgres` 풀은 운영 접속용으로 남김 | HA 템플릿의 `app_user` 예시, `SETUP_OPENSQL.md` §10과 같은 형태. #150 전까지 HA 측정은 전부 슈퍼유저로 했다 — 권한 경로가 검증에서 빠졌다 |
| 클라이언트 인증 | 지정 없음 → `md5`(OpenProxy 기본) | `auth_type = "scram-sha-256"` | HA 템플릿·문서 HA 예시 |
| `worker_threads` | 지정 없음 → 4(자동) | 2 | HA 템플릿 "DB와 같은 노드면 CPU 코어 수의 절반" — VM 4코어 |
| VIP (`[general.virtual_router]`) | 만들지 않음(`--owldb`에서만) | node2(우선순위 100)·node3(90), `unicast_peers`, `advert_int = 3` | 문서 「가상 IP 다중화」. 앱은 단일 엔드포인트로만 붙는다(ADR-006). `advert_int` 3은 그 페이지 예시값(HA 템플릿 주석 예시는 1) |
| systemd 등록 범위 | `ENABLE_SERVICE=etcd`(기본) — Patroni·OpenProxy는 `nohup` 프로세스 | Patroni·OpenProxy도 systemd | 설치기 공식 옵션 `ENABLE_SERVICE=true`와 같은 결과를 설치 뒤에 같은 템플릿으로 만들었다. 기본값으로는 재부팅한 노드가 클러스터에 돌아오지 않는다 |
| 풀이 바라보는 DB | `postgres` | `opensql` | 설치기가 `opensql` DB를 만들고 풀은 `postgres`를 본다 (§10 「풀이 바라보는 데이터베이스」) |

## 3. 공식과 달랐다가 되돌린 것

| 설정 | 기간 | 왜 넣었나 | 왜 되돌렸나 |
|---|---|---|---|
| 동기 복제 1대 (`synchronous_mode: true`, non-strict) | 2026-09-27 ~ 09-30 | "응답한 커밋은 잃지 않는다" — 실무 표준이라고 판단(ADR-049) | ① OpenProxy 1.1.3이 Patroni `/cluster`의 `sync_standby` 역할을 몰라 기동·설정 재로드가 실패한다(node2 재시작 루프) ② 문서·설치기 모두 비동기 ③ 9/21 멘토링 답: 고객 대부분은 성능 때문에 비동기 + 백업. → ADR-049 개정, #148 |
| `idle_in_transaction_session_timeout = 60s` (클러스터 전체) | 2026-09-28 ~ 09-30 | 위 keepalive와 같은 결함의 2차 방어선(ADR-051) | keepalive만으로 같은 결함이 약 60초에 풀리고, 클러스터 전체에 걸면 같은 DB의 다른 앱 세션까지 끊는다. 필요하면 OpenProxy 공식 설정 `idle_client_in_transaction_timeout`(풀 단위)을 쓴다. 걷은 뒤 OpenProxy 노드(VIP MASTER) 전원 차단 재측정 통과(유실 0·워커 정지 없음). → ADR-051 개정, #150 |
| OpenProxy `session` + 읽기/쓰기 분리 (실험 E2) | 2026-09-25 (node3만) | 설치기 `session` 모드를 유지한 채 분리만 켜 보려 했다 | 첫 쿼리로 서버가 고정돼 한 연결에서 SELECT 뒤 쓰기가 10/10 실패. 문서 경고와 일치 |

## 4. 공식 그대로 두지만 알아 둘 것

- **백업이 없다.** 설치기 `archive_command`는 `/bin/true`다. 문서는 비동기 복제의 유실을 백업(Barman 절)으로 메우는
  구성을 따로 둔다 — 이 환경에는 적용하지 않았다(별도 결정).
- **failover 유실 범위**는 `maximum_lag_on_failover`(1MB) 이내다. 측정은 모든 회차에서 유실 0이었지만 보장은 아니다.
- **동기 복제는 OpenProxy 1.1.3에서 쓸 수 없다.** 결함 기록은 `notes/ha110/openproxy-1.1.3-sync-standby.md`(로컬).

## 점검하는 법

```bash
# Patroni DCS — 설치기 bootstrap.dcs에 1절의 keepalive 5개만 더해져 있어야 한다
sudo -u opensql /home/opensql/bin/patronictl -c /home/opensql/etc/patroni/patroni.yml show-config
# OpenProxy가 실제로 적용한 설정(etcd 공유 설정 + 노드 파일)
sudo -u opensql /home/opensql/bin/openproxy --log-target file /home/opensql/etc/openproxy/openproxy.toml show --full
# 배포판 원본
cat opensql-installer/config/patroni.yml openproxy/openproxy.ha.template.toml
```

etcd에 저장된 공유 설정(`/service/opensql/openproxy/config/current`)은 기본값을 생략해 저장한다 — `pool_mode`가
안 보여도 `show --full`은 `Transaction`이다. 판단은 `show --full`로 한다.
