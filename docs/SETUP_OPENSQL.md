# OpenSQL 개발 환경 구축

> Tmax OpenSQL은 **x86-64 + Rocky Linux 9.7 전용**이다. Apple Silicon Mac에서는 가상머신이 필요하다.
> 이 문서는 그 환경을 만드는 절차다. 설계 배경은 [ADR-007](ADR.md), [ADR-020](ADR.md) 참조.

## 0. 구성 — DB만 가상머신에 둔다

전부를 VM 안에서 돌리지 않는다.

```
┌─ UTM VM (Rocky Linux 9.7 · x86-64 에뮬레이션) ─────────┐
│                                                        │
│   OpenSQL  =  PostgreSQL 17.8 + pgvector 0.8.1         │
│               OpenProxy · Patroni · etcd               │
│                                                        │
└────────────────────────┬───────────────────────────────┘
                         │  6432 (OpenProxy)
┌────────────────────────▼───────────────────────────────┐
│  macOS  (Apple Silicon 네이티브)                        │
│                                                        │
│   FastAPI  ·  임베딩 워커(BGE-M3)  ·  Next.js           │
│                                                        │
└────────────────────────────────────────────────────────┘
```

**이유**: Apple Silicon에서 x86-64 VM은 QEMU 전체 에뮬레이션이라 네이티브의 1/10~1/20 속도다. 임베딩 모델(BGE-M3, 2GB)을 그 안에서 돌리면 문서 하나 처리에 수 분이 걸려 실습이 불가능하다. **x86-64가 강제되는 것은 OpenSQL뿐**이므로 그것만 VM에 넣는다.

애플리케이션 코드는 바뀌지 않는다. `ADR-006`이 DSN을 환경변수로만 주입하도록 정해두었다.

```bash
DATABASE_URL="postgresql://<user>@<VM_IP>:6432/<pool_name>"
```

---

## 1. 준비물

| | 내용 |
|---|---|
| **UTM** | https://mac.getutm.app — 무료. App Store 판도 동일 |
| **Rocky Linux 9.7 x86-64 ISO** | `Rocky-9.7-x86_64-minimal.iso` (약 2.4GB)<br>`https://dl.rockylinux.org/vault/rocky/9.7/isos/x86_64/` |
| **OpenSQL 설치 파일** | `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720` |
| **라이선스 XML** | 대회 사무국에서 **개인 발급**. 이 저장소에 포함되지 않는다 |

> **버전을 정확히 맞출 것.** 9.7 전용 빌드다. 최신 릴리스(9.8 등)를 받으면 안 되며, `vault/` 경로에서 9.7을 받아야 한다.
> Minimal ISO를 쓴다. GUI가 없어 설치가 빠르고, 에뮬레이션 환경에서 데스크톱은 부담만 된다.

### 라이선스 확인

발급받은 XML에 검증 항목이 들어 있다.

```xml
<identified_by_host>opensql-dev</identified_by_host>   <!-- VM hostname과 일치해야 함 -->
<limit_cpu>4</limit_cpu>                                <!-- CPU 상한 -->
<end_date>2026/09/10</end_date>                         <!-- 만료일 -->
```

`identified_by_host` 값을 VM의 hostname으로 그대로 써야 한다. 틀리면 **PostgreSQL이 기동하지 않는다** — `patroni.yml`의 `shared_preload_libraries`에 `opensql_license`가 포함되어 라이선스 검증이 DB 기동 시점에 일어난다.

---

## 2. UTM 가상머신 생성

**⚠️ "Virtualize"가 아니라 "Emulate"를 선택한다.** Apple Silicon에서 Virtualize를 고르면 aarch64 VM이 만들어지고, x86-64 바이너리인 OpenSQL은 실행조차 되지 않는다.

```
UTM → Create a New Virtual Machine
  → Emulate                      ← Virtualize 아님
  → Linux
  → Boot ISO Image: Rocky-9.7-x86_64-minimal.iso
```

| 항목 | 값 | 비고 |
|---|---|---|
| Architecture | **x86_64** | |
| System | Standard PC (Q35) | 기본값 |
| Memory | 8192 MB | |
| CPU Cores | 4 | 라이선스 `limit_cpu=4` 이내 |
| Storage | 64 GB 이상 | OpenSQL + PG 데이터 + 문서/벡터 |
| Network | Shared Network (NAT) | 포트 포워딩은 3절 |

### CPU 토폴로지 (선택)

라이선스는 `limit_cpu` **상한**만 보므로 4코어 이하면 충분하다. 신청 시 제출한 값(소켓 1 / 코어 4 / 스레드 1)과 정확히 맞추고 싶으면 QEMU 인자를 추가한다.

```
VM 설정 → QEMU → Additional Arguments
-smp 4,sockets=1,cores=4,threads=1
```

게스트에서 `lscpu`로 확인할 수 있다.

---

## 3. Rocky Linux 설치

설치 마법사에서 **반드시** 지정할 것:

| 항목 | 값 |
|---|---|
| **Hostname** | `opensql-dev` — 라이선스 `identified_by_host`와 동일하게 |
| Software Selection | Minimal Install |
| Root Password / 사용자 | 임의 지정 (sudo 권한 부여) |
| Network | 활성화 (기본은 꺼져 있다) |

> 전체 에뮬레이션이라 설치에 **1~3시간** 걸릴 수 있다. 진행이 멈춘 것처럼 보여도 정상이다.

설치 후 hostname이 맞는지 확인한다.

```bash
hostnamectl                      # Static hostname: opensql-dev
# 다르면
sudo hostnamectl set-hostname opensql-dev
```

### 시간 동기화 (필수)

Patroni/etcd는 시간 동기화를 전제한다.

```bash
sudo dnf install -y chrony
sudo systemctl enable --now chronyd
chronyc tracking
```

### 방화벽

OpenSQL이 쓰는 포트를 연다.

```bash
sudo firewall-cmd --permanent --add-port={5432,6432,6433,2379,2380,8008}/tcp
sudo firewall-cmd --reload
```

| 포트 | 용도 |
|---|---|
| 5432 | PostgreSQL |
| **6432** | **OpenProxy — 애플리케이션이 접속하는 곳** |
| 6433 | OpenProxy 관리 |
| 2379 / 2380 | etcd |
| 8008 | Patroni REST API |

---

## 4. macOS ↔ VM 연결

UTM의 Shared Network는 NAT이라 맥에서 VM으로 직접 접속하려면 포트 포워딩이 필요하다.

```
VM 설정 → Network → Port Forwarding
  Guest 22   → Host 2222     (SSH)
  Guest 6432 → Host 6432     (OpenProxy)
```

맥에서 접속을 확인한다.

```bash
ssh -p 2222 <user>@localhost
```

> **VM 콘솔 대신 SSH를 쓴다.** 에뮬레이션 환경의 그래픽 콘솔은 느리고 복사·붙여넣기가 불편하다.

브리지 네트워크를 쓰면 VM이 로컬 네트워크 IP를 직접 받아 포트 포워딩이 불필요하다. 환경에 따라 선택한다.

---

## 5. 설치 파일 전송

```bash
# 맥에서
scp -P 2222 -r Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720 <user>@localhost:~/
scp -P 2222 OpenSQL_Trial_opensql-dev_*.xml \
    <user>@localhost:~/Tmax_OpenSQL_.../opensql-installer/licenses/
```

라이선스는 반드시 `opensql-installer/licenses/` 아래에 둔다.

---

## 6. OpenSQL 설치 (single 모드)

### 6-1. 설정

`opensql-installer/config/common.env`를 편집한다.

```bash
NODE1_IP="10.0.2.15"                      # ip addr 로 확인한 VM IP
NODE_NAME="opensql-dev"
OPENSQL_HOME="/home/opensql"
PG_HOME="/home/opensql"
PG_DATA_DIR="/home/opensql/data/pgsql"
LICENSE_NAME="OpenSQL_Trial_opensql-dev_20260910.xml"   # 실제 파일명
```

파일명을 `node1_license.xml` 등으로 바꿀 필요는 없다. `LICENSE_NAME`에 실제 이름을 적으면 된다.

### 6-2. 실행

```bash
cd ~/Tmax_OpenSQL_*/opensql-installer
python3 opensql_local_installer.py --mode single
```

single 모드는 **PostgreSQL · Patroni · etcd · OpenProxy를 모두** 한 노드에 설치한다. `INSTALL_OPENPROXY=false`로 OpenProxy를 뺄 수 있지만, 이 프로젝트는 OpenProxy 경유를 전제하므로(ADR-006) 그대로 둔다.

> single 모드에서는 **OpenProxy VIP failover가 비활성화**된다. VIP 없이 VM IP로 직접 접속한다.

### 6-3. 확인

```bash
patronictl -c $OPENSQL_HOME/etc/patroni.yml list
psql -h <VM_IP> -p 6432 -U <user> <pool_name> -c "SELECT version();"
```

---

## 7. 설치 후 검증 (M0)

`OPENSQL_RESEARCH.md` §12의 미확인 항목을 실제로 확인한다. 배포판 METADATA로 이미 해결된 항목은 재확인만 하면 된다.

```sql
-- 버전 (METADATA 기준: PG 17.8, pgvector 0.8.1, pgvectorscale 0.9.0)
SELECT version();
SELECT extname, extversion FROM pg_extension;
SELECT name, default_version FROM pg_available_extensions WHERE name LIKE '%vector%';

-- HNSW 인덱스 생성 (pgvector 0.8.1이므로 가능해야 함)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE _t (id int, v vector(1024));
CREATE INDEX ON _t USING hnsw (v vector_cosine_ops);
DROP TABLE _t;

-- avg(vector) 지원 (ADR-018)
SELECT avg(v) FROM (SELECT '[1,2,3]'::vector AS v) s;

-- 연결 제약
SHOW max_connections;   -- patroni.yml 기준 100
```

**실측이 필요한 항목** (문서로 알 수 없음):

| # | 항목 | 방법 |
|---|---|---|
| 1 | OpenProxy 경유 `LISTEN`/`NOTIFY` 동작 | 한 세션에서 `LISTEN ch`, 다른 세션에서 `NOTIFY ch` |
| 6 | LISTEN 연결의 유휴 타임아웃 | 장시간 유휴 후 수신 여부 |
| 12 | `avg` 결과가 HNSW 인덱스를 타는지 | 관련 문서 쿼리에 `EXPLAIN (ANALYZE, BUFFERS)` |
| 13 | `pg_trgm` 설치 가능 여부 | `CREATE EXTENSION pg_trgm` |

**Failover 관련 항목(7·8)은 검증할 수 없다.** single 구성은 노드가 1대라 승격할 replica가 없다. 대응은 [ADR-020](ADR.md) 참조.

---

## 8. 알려진 제약

| 제약 | 영향 |
|---|---|
| **x86-64 에뮬레이션** | 부팅·쿼리가 느리다. 애플리케이션을 맥 네이티브로 두는 이유 |
| **single 구성** | 실제 failover 시연 불가 (기업 지시사항) |
| **라이선스 hostname 고정** | `opensql-dev` 외의 hostname에서는 DB가 기동하지 않는다 |
| **라이선스 만료 2026/09/10** | 이후 DB 기동 불가. 필요 시 사무국에 연장 요청 |

## 9. 로컬 대체 환경

OpenSQL VM 없이도 DB 계층 대부분을 개발·테스트할 수 있다 (ADR-007).

```bash
docker compose up -d      # pgvector/pgvector:pg17 단일 컨테이너
```

| 로컬 컨테이너로 가능 | VM(OpenSQL)이 필요 |
|---|---|
| 트리거·아웃박스·파셜 유니크 인덱스·CASCADE | OpenProxy 경유 세션 상태 (ADR-009) |
| `FOR UPDATE SKIP LOCKED` 워커 경쟁 | 읽기/쓰기 분리 동작 (ADR-010) |
| 청킹·임베딩·검색 SQL | Patroni 운영 명령 |
| DB 직결 `LISTEN`/`NOTIFY` | 라이선스·번들 확장 실동작 |

일상 개발은 컨테이너로 하고, OpenSQL 고유 동작을 확인할 때만 VM을 쓰는 것이 현실적이다.
