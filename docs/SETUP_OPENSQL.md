# OpenSQL 개발 환경 구축

> Tmax OpenSQL은 **x86-64 + Rocky Linux 9.7 전용**이다. Apple Silicon Mac에서는 가상머신이 필요하다.
> 설계 배경은 [ADR-007](ADR.md), [ADR-020](ADR.md). 이 문서는 **2026-08-05에 실제로 설치하며 검증한 절차**이며, 막혔던 지점을 ⚠️로 표시했다.

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
DATABASE_URL="postgresql://postgres:pg_password@<VM_IP>:6432/opensql"
```

---

## 1. 준비물

| | 내용 |
|---|---|
| **UTM** | https://mac.getutm.app — 무료 |
| **Rocky Linux 9.7 x86-64 ISO** | `Rocky-9.7-x86_64-minimal.iso` (약 2.4GB)<br>`https://dl.rockylinux.org/vault/rocky/9.7/isos/x86_64/` |
| **OpenSQL 설치 파일** | `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720` |
| **라이선스 XML** | 대회 사무국에서 **개인 발급**. 이 저장소에 포함되지 않는다 |

> **버전을 정확히 맞출 것.** 9.7 전용 빌드다. 최신 릴리스를 받으면 안 되며 `vault/` 경로에서 9.7을 받는다.

### 라이선스 확인

```xml
<identified_by_host>opensql-dev</identified_by_host>   <!-- VM hostname과 일치해야 함 -->
<limit_cpu>4</limit_cpu>                                <!-- CPU 상한. 정확히 일치가 아니라 이하 -->
<end_date>2026/09/10</end_date>                         <!-- 만료일 -->
```

`identified_by_host`를 VM hostname으로 그대로 쓴다. 틀리면 **PostgreSQL이 기동하지 않는다** — `patroni.yml`의 `shared_preload_libraries`에 `opensql_license`가 있어 검증이 DB 기동 시점에 일어난다.

`limit_cpu`는 **상한**이므로 4코어 이하면 된다. 소켓/코어/스레드 토폴로지를 정확히 맞출 필요는 없다.

---

## 2. UTM 가상머신 생성

⚠️ **"Virtualize"가 아니라 "Emulate"를 선택한다.** Apple Silicon에서 Virtualize를 고르면 aarch64 VM이 만들어지고, x86-64 바이너리인 OpenSQL은 실행되지 않는다. 설치 스크립트에도 `OPENSQL_RUST_TOOLCHAIN="1.85.0-x86_64-unknown-linux-gnu"`가 하드코딩되어 있어 Rosetta 우회도 불가능하다.

```
UTM → Create a New Virtual Machine → Emulate → Linux
  → Boot ISO Image: Rocky-9.7-x86_64-minimal.iso
```

| 항목 | 값 |
|---|---|
| Architecture | **x86_64** |
| System | Standard PC (Q35) |
| Memory | 8192 MB |
| CPU Cores | 4 (라이선스 `limit_cpu` 이내) |
| Storage | 64 GB 이상 |
| Network | Shared Network |
| **공유 디렉토리** | **설정하지 않는다** (아래) |

### 공유 디렉토리는 건너뛴다

파일 전송은 `scp`로 한다. Rocky Minimal에는 9p/SPICE 클라이언트가 없어 게스트 드라이버 설치가 추가로 필요하고, SSH는 어차피 켜야 하며, 전송은 일회성이다.

### VM 이름과 hostname은 다르다

UTM의 VM 이름은 아무거나 상관없다. 라이선스가 검증하는 것은 **게스트 OS의 hostname**이며 3절에서 지정한다.

---

## 3. Rocky Linux 설치

⚠️ **GRUB 첫 화면에서 맨 위 `Install Rocky Linux Minimal 9.7`을 선택한다.** 기본 선택은 `Test this media & install`인데, 에뮬레이션 환경에서 2.4GB 무결성 검사에 수십 분이 걸린다. 이미 시작됐다면 `ESC`로 건너뛸 수 있다.

설치 마법사에서 지정할 것:

| 항목 | 값 |
|---|---|
| **Network & Host Name** | hostname을 **`opensql-dev`**로 입력하고 **[적용]** 클릭. Ethernet 토글도 **켠다**(기본 꺼짐) |
| Software Selection | Minimal Install |
| 사용자 생성 | 관리자 체크. ⚠️ **계정명을 `opensql`로 하지 말 것** — 설치기가 그 이름으로 유저를 만든다 |
| root 비밀번호 | 설정 권장(콘솔 복구용). "root SSH 로그인 허용"은 체크하지 않는다 |

> 전체 에뮬레이션이라 설치에 **1~3시간** 걸린다. 멈춘 것처럼 보여도 정상이다.

⚠️ **설치 후 재부팅하면 다시 설치 메뉴가 뜬다.** ISO가 드라이브에 남아 CD로 부팅하기 때문이다.

```
VM 정지 → UTM 설정 → 드라이브 → [초기화] → [저장]
```

`제거`가 아니라 `초기화`를 쓴다. 드라이브는 남고 ISO만 빠져 나중에 복구 부팅에 쓸 수 있다.

---

## 4. 네트워크 설정

### ⚠️ 콘솔 키보드 문제를 먼저 알아둘 것

설치 시 키보드를 "한국어"로 골랐다면 콘솔에서 **`/` `{` `}` 가 입력되지 않는다.** 증상이 "명령이 중간에 잘림"이라 원인을 찾기 어렵다.

```bash
sudo localectl set-keymap us
```

**초기 설정만 콘솔에서 하고 곧바로 SSH로 옮기는 것을 권한다.**

### IP 확인과 고정

```bash
ip addr show | grep "inet "       # 예: 192.168.64.4
ip route | grep default           # 예: default via 192.168.64.1 dev enp0s1
sudo systemctl enable --now sshd
```

DHCP 주소는 재시작 시 바뀔 수 있다. **바뀌면 `patroni.yml`·`openproxy.toml`·etcd 설정이 어긋나 클러스터가 뜨지 않으므로** 설치 전에 고정한다.

```bash
CON=$(nmcli -g GENERAL.CONNECTION dev show enp0s1)
echo "$CON"
```

```bash
sudo nmcli con mod "$CON" ipv4.addresses 192.168.64.4/24
sudo nmcli con mod "$CON" ipv4.gateway 192.168.64.1
sudo nmcli con mod "$CON" ipv4.dns "8.8.8.8 1.1.1.1"
sudo nmcli con mod "$CON" ipv4.method manual
sudo nmcli con up "$CON"
```

⚠️ **한 줄씩 나눠 실행한다.** 줄 끝 백슬래시로 이어 쓰면 붙여넣기 과정에서 공백이 누락돼 `24ipv4.gateway` 같은 값이 만들어진다.

⚠️ **SSH에서 실행하면 `nmcli con up` 시점에 연결이 끊긴다.** `con mod`는 설정만 저장하므로 안전하고, 마지막 줄만 분리하면 된다.

```bash
sudo systemd-run --on-active=3 nmcli con up "$CON"
```

확인:

```bash
ip addr show enp0s1 | grep "inet "     # dynamic 이 사라져야 한다
ping -c 2 8.8.8.8
ping -c 2 google.com                    # DNS까지. 실패하면 dnf 가 막힌다
```

### 맥에서 SSH

`192.168.64.x`는 UTM Shared Network 대역이라 **맥에서 직접 접근된다.** 포트 포워딩이 필요 없다.

```bash
ssh kje@192.168.64.4
```

안 되면 UTM 포트 포워딩(Guest 22 → Host 2222)을 설정하고 `ssh -p 2222 kje@localhost`로 붙는다.

### 시간 동기화와 방화벽

```bash
sudo dnf install -y chrony
sudo systemctl enable --now chronyd
chronyc tracking          # Reference ID 에 서버가 나오고 Stratum 이 1~4면 정상
```

```bash
sudo firewall-cmd --permanent --add-port={5432,6432,6433,2379,2380,8008}/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

| 포트 | 용도 |
|---|---|
| 5432 | PostgreSQL |
| **6432** | **OpenProxy — 애플리케이션 접속 지점** |
| 6433 | OpenProxy 관리 |
| 2379 / 2380 | etcd |
| 8008 | Patroni REST API |

---

## 5. ⚠️ dnf 저장소를 9.7로 고정 (필수)

**9.7 ISO로 설치해도 dnf는 최신(9.8)을 본다.** 이 상태로 개발 패키지를 설치하면 glibc 충돌이 난다.

```
package gcc-toolset-15-gcc ... requires glibc-devel >= 2.2.90-12
cannot install both glibc-common-2.34-275.el9_8 and glibc-common-2.34-266.el9_8
```

OpenSQL이 9.7 전용이므로 저장소도 9.7로 맞춘다.

```bash
sudo cp -a /etc/yum.repos.d /etc/yum.repos.d.bak

echo "vault/rocky" | sudo tee /etc/dnf/vars/contentdir
echo "9.7"         | sudo tee /etc/dnf/vars/releasever

sudo sed -i -e 's|^mirrorlist=|#mirrorlist=|' \
            -e 's|^#baseurl=http://dl.rockylinux.org|baseurl=https://dl.rockylinux.org|' \
            /etc/yum.repos.d/rocky*.repo

sudo dnf clean all
sudo dnf makecache
```

확인:

```bash
dnf repolist          # Rocky Linux 9.7 - BaseOS / AppStream / Extras
dnf list glibc        # el9_7 계열이어야 한다. el9_8 이면 실패
```

> ⚠️ **`sudo dnf update`를 실행하지 말 것.** 시스템이 9.8로 올라가면 지원 범위를 벗어난다.

되돌리기:

```bash
sudo rm -rf /etc/yum.repos.d && sudo mv /etc/yum.repos.d.bak /etc/yum.repos.d
sudo rm -f /etc/dnf/vars/contentdir /etc/dnf/vars/releasever && sudo dnf clean all
```

---

## 6. ⚠️ 필수 패키지 설치

`AUTO_INSTALL_PREREQS`가 기본 `false`라 설치기가 자동으로 깔지 않는다. **Minimal 설치에는 `tar`조차 없어** 설치기가 16%에서 `exit=127`로 죽는다.

```bash
sudo dnf install -y tar gcc make gettext jq pkgconf-pkg-config \
                    openssl-devel clang-devel python3-psycopg2 \
                    bison flex krb5-devel lz4-devel protobuf-c \
                    readline-devel zlib-devel \
                    libxml2 lz4-libs ncurses-libs readline zlib \
                    libxslt perl-libs
```

PostGIS 의존성은 EPEL에서 받는다.

```bash
sudo dnf install -y epel-release
sudo dnf install -y geos proj gdal SFCGAL
```

`-devel`이 아니다 — 요구 목록에 있는 것은 런타임 패키지다.

---

## 7. 설치 파일 전송

```bash
# 맥에서
scp -r Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720 kje@192.168.64.4:~/
scp OpenSQL_Trial_opensql-dev_*.xml \
    kje@192.168.64.4:~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer/licenses/
```

3GB라 시간이 걸린다. 전송 후 크기를 대조해 누락을 확인한다.

```bash
du -sh ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720
```

---

## 8. ⚠️ SFCGAL 버전 요구 완화

검증 단계에서 마지막으로 막히는 지점이다.

```
REQUIRED_BAD=SFCGAL:1.5.0-1.el9<2.0.0
```

요구는 `2.0.0` 이상인데 EPEL 9에는 `1.5.0`뿐이다 — PGDG 저장소를 전제한 요구사항이다.

**PGDG를 추가하지 말 것.** PostgreSQL 패키지가 겹쳐 OpenSQL 자체 PG 17.8과 충돌할 위험이 있다. SFCGAL은 **PostGIS 전용**이고 이 프로젝트는 PostGIS를 쓰지 않으므로 요구 버전만 완화한다.

```bash
cd ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer
cp src/package_requirements.json src/package_requirements.json.bak
```

```bash
jq '(..|objects|select(.name_pattern=="SFCGAL" or .name=="SFCGAL")|.min_version)="1.5.0"' src/package_requirements.json >/tmp/pr.json
```

```bash
mv /tmp/pr.json src/package_requirements.json
grep -A2 SFCGAL src/package_requirements.json | head
```

> 이후 PostGIS **설치 단계**에서 다시 막히면 요구사항 자체를 제거한다.
> ```bash
> jq 'with_entries(.value |= del(.postgis))' src/package_requirements.json >/tmp/pr.json && mv /tmp/pr.json src/package_requirements.json
> ```

---

## 9. OpenSQL 설치 (single 모드)

`config/common.env`를 채운다. `vi`보다 `sed`가 확실하다.

```bash
cd ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer

sed -i 's|^NODE1_IP=.*|NODE1_IP="192.168.64.4"|'                   config/common.env
sed -i 's|^NODE_NAME=.*|NODE_NAME="opensql-dev"|'                  config/common.env
sed -i 's|^OPENSQL_HOME=.*|OPENSQL_HOME="/home/opensql"|'          config/common.env
sed -i 's|^PG_HOME=.*|PG_HOME="/home/opensql"|'                    config/common.env
sed -i 's|^PG_DATA_DIR=.*|PG_DATA_DIR="/home/opensql/data/pgsql"|' config/common.env
sed -i 's|^LICENSE_NAME=.*|LICENSE_NAME="OpenSQL_Trial_opensql-dev_20260910.xml"|' config/common.env

grep -E "^(NODE1_IP|NODE_NAME|OPENSQL_HOME|PG_HOME|PG_DATA_DIR|LICENSE_NAME)=" config/common.env
ls licenses/
```

라이선스 파일명은 그대로 두고 `LICENSE_NAME`에 실제 이름을 적으면 된다.

```bash
python3 opensql_local_installer.py --mode single
```

single 모드는 **PostgreSQL · Patroni · etcd · OpenProxy를 모두** 한 노드에 설치한다. VIP failover는 비활성화된다.

> 중간에 실패해 재실행할 때 "이미 존재한다"류 에러가 나면 `sudo rm -rf /home/opensql/install` 후 다시 시도한다. `opensql` 유저는 지우지 않아도 된다.

---

## 10. 설치 확인

⚠️ **운영은 `opensql` 유저로 한다.** `.opensqlrc`가 그 계정 홈에 있어 환경변수가 자동 로드된다.

```bash
sudo su - opensql
echo "$OPENSQL_HOME / $PG_HOME / $PG_DATA_DIR"
```

⚠️ **`patroni.yml` 경로가 한 단계 더 깊다.**

```bash
patronictl -c $OPENSQL_HOME/etc/patroni/patroni.yml list
```

```
+ Cluster: opensql ------------------------------+----+-----------+
| Member      | Host         | Role   | State   | TL | Lag in MB |
| postgresql1 | 192.168.64.4 | Leader | running |  1 |           |
```

**여기까지 오면 라이선스 검증을 통과한 것이다.**

⚠️ **psql은 소켓 접속이 편하다.** `pg_hba`가 `local all all trust`라 비밀번호를 묻지 않는다. `-h`를 붙이면 TCP가 되어 `md5` 인증(비밀번호 `pg_password`)을 요구한다.

```bash
psql -U postgres -c "SELECT version();"
```

### OpenProxy 경유 접속

⚠️ **`-d`에는 DB 이름이 아니라 pool 이름을 넣는다.**

```bash
PGPASSWORD=pg_password psql -h 192.168.64.4 -p 6432 -U postgres -d opensql -c "SELECT 1;"
```

설치기가 생성한 pool 설정은 `$OPENSQL_HOME/etc/openproxy/openproxy.toml`에 있다.

```toml
[pools.opensql]
pool_mode = "session"
query_parser_enabled = false
[pools.opensql.users.0]
username = "postgres"
password = "pg_password"
pool_size = 10
```

> ⚠️ `pg_hba`에 `host all all all md5`가 있고 비밀번호가 기본값(`pg_password`)이다. 애플리케이션 계정을 만들 때 함께 정리한다.

### ⚠️ 풀이 바라보는 데이터베이스를 교정한다 (필수)

설치기는 `opensql` 데이터베이스를 만들어놓고, 정작 풀은 관리용 기본 DB인 `postgres`를 바라보게 설정한다.
클라이언트는 DSN에 **풀 이름**을 적으므로 실제 저장 위치가 드러나지 않는다 — 그대로 두면
마이그레이션과 애플리케이션 데이터가 `postgres`에 쌓인다.

```bash
CONF=$OPENSQL_HOME/etc/openproxy/openproxy.toml
cp $CONF $CONF.bak.$(date +%Y%m%d-%H%M%S)
sed -i 's|^database = "postgres"$|database = "opensql"|' $CONF
diff $CONF.bak.* $CONF          # 23행 한 줄만 바뀌어야 한다
bash $OPENSQL_HOME/scripts/restart_openproxy.sh
```

`reload`(SIGHUP)가 아니라 **restart**를 쓴다. 이미 열린 백엔드 연결이 옛 DB를 향한 채 재사용될 수 있다.

```bash
PGPASSWORD=pg_password psql -h <VM_IP> -p 6432 -U postgres -d opensql \
  -c "SELECT current_database(), count(*) FROM pg_stat_user_tables"
```

`opensql | 0`이 나오면 반영된 것이다. **`DATABASE_URL`은 바뀌지 않는다** — 풀 이름은 그대로다.

---

## 11. 설치 후 검증

`OPENSQL_RESEARCH.md` §12의 항목을 실행한다. **2026-08-05 실측에서 아래가 모두 통과했다.**

```bash
psql -U postgres -c "SELECT version();"                                     # 17.8
psql -U postgres -c "SELECT name, default_version FROM pg_available_extensions WHERE name LIKE '%vector%';"
                                                                            # vector 0.8.1 / vectorscale 0.9.0
psql -U postgres -c "SHOW max_connections;"                                 # 100

psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -U postgres -c "CREATE TABLE _t (id int, v vector(1024));"
psql -U postgres -c "CREATE INDEX ON _t USING hnsw (v vector_cosine_ops);"  # ADR-002
psql -U postgres -c "SELECT avg(v) FROM (SELECT '[1,2,3]'::vector AS v) s;" # ADR-018
psql -U postgres -c "DROP TABLE _t;"

psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"               # ADR-016
```

### LISTEN/NOTIFY (터미널 2개 필요)

```bash
# 터미널 1 — OpenProxy 경유
PGPASSWORD=pg_password psql -h 192.168.64.4 -p 6432 -U postgres -d opensql
opensql=> LISTEN ch1;
opensql=> SELECT 1;        -- ⚠️ 이 쿼리가 결과를 왜곡한다 — 아래 경고 참조
```

```bash
# 터미널 2
psql -U postgres -c "NOTIFY ch1, 'hello from another session';"
```

> ⚠️ **이 절차로는 판정할 수 없다.** 알림은 수신되지만, 그것은 `SELECT 1`이 지연된 알림을 밀어냈기
> 때문이다. 대화형 psql은 사용자가 계속 입력을 보내므로 **유휴 클라이언트를 재현하지 못한다.**
> 재실측 결과 **OpenProxy(6432) 경유로는 유휴 세션에 알림이 전달되지 않는다** — 노드 직결(5432)
> 에서만 즉시 도착한다 (`OPENSQL_RESEARCH.md` §7-3). 유휴 수신을 확인하려면 쿼리를 보내지 않는
> 비대화형 클라이언트로 측정해야 한다.

`pool_mode = "session"`이라 세션 상태 자체는 보존된다. 설계 영향은 `ADR-009` — **워커는 폴링을 주
경로로 유지하며, 이 결과와 무관하게 파이프라인이 동작한다.**

### 아직 측정하지 못한 것

| 항목 | 사유 |
|---|---|
| ~~LISTEN 연결의 `idle_timeout` 실동작~~ | ✅ **측정 완료** — 유휴 세션은 70분간 끊기지 않았다. 다만 애초에 알림이 오지 않아 **폴링 주기 상향은 철회됐다** (`OPENSQL_RESEARCH.md` §12 6번) |
| ~~`avg`가 HNSW 인덱스를 타는지~~ | ✅ **측정 완료** — `avg`는 인덱스를 막지 않는다 (`OPENSQL_RESEARCH.md` §12 12번) |
| Failover | ⛔ Single 구성이라 원리적으로 불가 (ADR-020) |

---

## 12. 알려진 제약

| 제약 | 영향 |
|---|---|
| **x86-64 에뮬레이션** | 부팅·쿼리가 느리다. 애플리케이션을 맥 네이티브로 두는 이유 |
| **single 구성** | 실제 failover 시연 불가 (사무국 지시사항) |
| **라이선스 hostname 고정** | `opensql-dev` 외의 hostname에서는 DB가 기동하지 않는다 |
| **라이선스 만료 2026/09/10** | 이후 DB 기동 불가. 1차 제출(8/27)까지는 여유가 있으나 2차 일정 확인 필요 (ADR-021) |

## 13. 로컬 대체 환경

OpenSQL VM 없이도 DB 계층 대부분을 개발·테스트할 수 있다 (ADR-007).

```bash
docker compose up -d      # pgvector/pgvector:pg17 단일 컨테이너
```

| 로컬 컨테이너로 가능 | VM(OpenSQL)이 필요 |
|---|---|
| 트리거·아웃박스·파셜 유니크 인덱스·CASCADE | OpenProxy 경유 세션 동작 (ADR-009) |
| `FOR UPDATE SKIP LOCKED` 워커 경쟁 | 읽기/쓰기 분리 설정 확인 (ADR-010) |
| 청킹·임베딩·검색 SQL | Patroni 운영 명령 |
| DB 직결 `LISTEN`/`NOTIFY` | 라이선스·번들 확장 실동작 |

일상 개발은 컨테이너로 하고, OpenSQL 고유 동작을 확인할 때만 VM을 쓴다.

---

## 부록: 붙여넣기 주의

에뮬레이션 콘솔과 SSH 모두에서 겪은 문제다.

- **heredoc**(`<<'PY'`)은 붙여넣기 시 각 줄에 들여쓰기가 붙어 종료 마커를 인식하지 못한다
- **긴 한 줄 명령**은 터미널 폭에서 잘려 `&&` 뒤가 별도 명령으로 실행된다
- **줄 끝 백슬래시**로 이어 쓰면 공백이 누락돼 인자가 붙어버린다 (`192.168.64.4/24\` + `ipv4.gateway` → `24ipv4.gateway`)

**여러 줄 명령은 한 줄씩 나눠 실행한다.**
