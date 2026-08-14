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

## 14. AWS EC2에 같은 환경 만들기

**2026-08-09 실제로 설치해 확인했다.** 배포를 위해 클라우드에서 OpenSQL이 기동하는지가 미확인 상태였고, 된다.

### VM보다 오히려 쉽다

| | UTM VM | AWS EC2 |
|---|---|---|
| OS 준비 | ISO로 설치, hostname 수동 설정 | **Rocky 9.7 AMI가 그대로 있다** |
| §5 dnf 9.7 고정 | 필수 (9.8이면 glibc 충돌) | **불필요** — AMI가 이미 9.7 |
| 네트워크(§4) | 고정 IP·방화벽 수동 설정 | 보안그룹만 |
| 아키텍처 | Apple Silicon에서 x86-64 에뮬레이션 | 네이티브 x86-64 |

라이선스가 묶는 것은 `<identified_by_host>`(hostname)와 `<limit_cpu>`뿐이다 — MAC·IP·machine-id는 보지 않는다. **hostname을 맞추고 CPU를 상한 이하로 잡으면 어느 머신에서든 뜬다.**

### 인스턴스 생성

hostname은 cloud-init으로 잡는다. 라이선스가 이 이름에 묶여 있어 다르면 PostgreSQL이 기동하지 않는다.

```bash
cat > /tmp/user-data.yaml <<'YAML'
#cloud-config
preserve_hostname: false
hostname: opensql-dev
fqdn: opensql-dev
manage_etc_hosts: true
YAML
```

AMI ID는 리전마다 다르다. 이름으로 조회한다.

```bash
aws ec2 describe-images --region ap-northeast-2 --owners 792107900819 \
  --filters "Name=name,Values=Rocky-9-EC2-Base-9.7-*x86_64*" "Name=state,Values=available" \
  --query 'sort_by(Images,&CreationDate)[-1].[ImageId,Name]' --output text
```

> 2026-08-09 기준 서울 리전은 `ami-0ed6cd3fecc849a03` (`Rocky-9-EC2-Base-9.7-20251123.2.x86_64`)이다.

```bash
aws ec2 run-instances --region ap-northeast-2 \
  --image-id <위에서 조회한 AMI> \
  --instance-type t3.large \
  --key-name <키페어> \
  --security-group-ids <보안그룹> \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":40,"VolumeType":"gp3"}}]' \
  --user-data file:///tmp/user-data.yaml \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=opensql-dev}]'
```

**보안그룹은 SSH(22)만 본인 IP로 연다.** 5432·6432·2379·8008을 외부에 열지 않는다 — DB 비밀번호가 기본값(`pg_password`)인 상태이고, 애플리케이션은 어차피 같은 호스트나 같은 VPC에서 붙는다.

### 설치

설치 파일(897MB)은 상용 배포판이라 저장소에 없다. 기존 VM에서 스트리밍하는 것이 가장 빠르다.

```bash
ssh <vm> "tar cf - -C ~ Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720" \
  | gzip -1 \
  | ssh -i <키> rocky@<EC2> "gunzip | tar xf - -C ~"
```

이후는 스크립트가 §6·§8·§9를 한 번에 처리한다. 사전 조건(아키텍처·OS 버전·hostname·CPU 수)을 먼저 검사하므로, 설치기가 도중에 죽고 원인을 찾는 일이 없다.

```bash
bash scripts/install_opensql_host.sh ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720
```

### 확인된 결과 (t3.large, 2 vCPU / 8GB)

| 항목 | 결과 |
|---|---|
| `patronictl list` | `postgresql1 · Leader · running · TL 1` |
| PostgreSQL | 17.8 x86_64 — VM과 동일 |
| 4개 컴포넌트 | PostgreSQL · Patroni · etcd · OpenProxy 전부 기동 |
| 라이선스 | 통과. `opensql_license checker` 동작 |
| `shared_preload_libraries` | **VM과 완전히 동일한 12종** — `pg_cron`·`pgaudit`·`pg_hint_plan` 포함 |

마지막 줄이 중요하다. 번들 확장을 채택하기로 결정하면 **배포 환경에서도 그대로 쓸 수 있다.**

### 제약

- **라이선스는 2026-09-10에 만료된다.** 그 후에는 이 인스턴스의 PostgreSQL도 기동하지 않는다 (ADR-021)
- **인스턴스를 정지했다 켜면 공인 IP가 바뀐다.** 심사용 링크를 유지하려면 Elastic IP나 도메인이 필요하다
  - 반면 **사설 IP는 보존된다**(실측: 정지·기동 후에도 `172.31.25.213`). `patroni.yml`·`openproxy.toml`이 사설 IP에 묶여 있으므로 재설정 없이 그대로 뜬다
- **재부팅하면 `opensql-etcd.service` 하나만 살아난다.** Patroni·PostgreSQL·OpenProxy는 systemd 유닛이 없는 `nohup` 맨 프로세스라(#27) 인스턴스를 켤 때마다 수동 기동이 필요하다 (§15)
- **CPU는 4개를 넘길 수 없다.** 라이선스 `<limit_cpu>` 상한이라 t3.xlarge(4 vCPU)가 최대다
- 설치 파일과 라이선스 xml은 **저장소에 커밋하지 않는다.** 저장소는 규정상 public이어야 한다 (`PROJECT_CONTEXT.md` 제10조 ②)

---

## 15. 앱을 배포해 공개 URL로 띄우기

**2026-08-09 실제로 관통했다.** DB만 뜨는 것과 앱이 공개 URL에서 도는 것은 별개였고, 된다.

업로드 → 임베딩 → 검색이 맥에서 EC2 공인 IP로 전부 확인됐다.

### 인스턴스를 켤 때마다: OpenSQL 먼저

재부팅 후 살아나는 것은 etcd뿐이다. 순서가 있다 — Patroni가 PostgreSQL을 띄우고, OpenProxy가 그 뒤에 붙는다.

```bash
sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_patroni.sh'
sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_openproxy.sh'
sudo -u opensql /home/opensql/bin/patronictl -c /home/opensql/etc/patroni/patroni.yml list
```

`Leader · running`이 나오면 된다. **재기동할 때마다 `TL`(timeline)이 1씩 오른다** — 장애가 아니라 정상이다.

### 런타임 (최초 1회)

Rocky 9.7 기본은 Python 3.9이고 Node는 없다. 둘 다 dnf에 있다.

```bash
sudo dnf install -y python3.12 python3.12-devel
sudo dnf module install -y nodejs:22/common
```

> `python3.12`는 `el9_8` 빌드로 잡히지만 **glibc를 건드리지 않는다**(sqlite-libs만 올라간다). 애초에 이 AMI의 glibc가 이미 `2.34-275.el9_8`이고 OpenSQL은 그 위에서 돈다 — §5의 9.7 고정은 VM에서 ISO로 설치할 때의 이야기다.

### 소스 전송 (맥에서)

저장소가 아직 public이 아니라 `git clone` 대신 rsync로 밀어 넣는다.

```bash
rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='node_modules' \
  --exclude='__pycache__' --exclude='.next' --exclude='*.egg-info' \
  --exclude='.pytest_cache' --exclude='*.pem' \
  -e "ssh -i ~/.ssh/<키>.pem" \
  ./ rocky@<EC2 공인 IP>:~/OpenArchive/
```

### 배포 (EC2에서)

```bash
cd ~/OpenArchive && bash scripts/deploy_app_host.sh
```

venv 구성·프론트 빌드·3종 기동·헬스체크까지 한 번에 한다. **재실행 47초**(의존성이 이미 받아져 있을 때). DB가 안 떠 있으면 위 기동 명령을 안내하고 멈춘다.

### 보안그룹

```bash
aws ec2 authorize-security-group-ingress --region ap-northeast-2 \
  --group-id <SG> --protocol tcp --port 3000 --cidr <내 IP>/32
```

**3000만 연다.** API(8000)는 `127.0.0.1`에만 바인딩하고 Next.js가 `/api/*`를 rewrite로 프록시한다(`next.config.ts`). 신원은 서버가 발급한 세션 쿠키의 검증으로만 해석되므로(ADR-028) 익명 요청은 public 문서까지만 닿지만, API를 직접 열면 로그인·쓰기 엔드포인트가 그대로 인터넷에 노출된다. 공개면은 프론트 하나로 좁혀 둔다.

로그인은 ADR-028에서 들어갔다. 그래도 확인용으로 열었다면 끝나고 규칙을 지운다 — 열어둔 상태를 습관으로 남기지 않는다.

```bash
aws ec2 revoke-security-group-ingress --region ap-northeast-2 \
  --group-id <SG> --protocol tcp --port 3000 --cidr <내 IP>/32
```

### 알고 있을 것

- **CPU 전용 torch를 먼저 깐다.** PyPI 기본 wheel은 CUDA 빌드라 GPU 없는 인스턴스에 `nvidia-*` 2.7GB가 따라온다. 순서를 지키면 venv가 **5.1GB → 1.4GB**, 설치가 **2m37s → 1m16s**로 준다. `deploy_app_host.sh`가 이미 그렇게 한다
- **BGE-M3가 두 벌 올라간다.** 워커(2233MB)와 API(2006MB)가 각각 모델을 로드한다 — 워커는 청크를, API는 검색 질의를 임베딩하기 때문이다. t3.large 8GB에서 **합계 4.2GB**로 여유가 좁고, swap이 없다. 모델 캐시는 디스크 4.3GB
- **첫 검색만 느리다.** API 프로세스가 모델을 lazy load 하므로 재기동 후 첫 질의가 **16~19초**, 이후는 **0.4~0.5초**다. 시연 전에 질의를 한 번 흘려 예열한다
- **재부팅 생존은 없다.** 앱도 `nohup` 맨 프로세스다. OpenSQL 자신이 그렇게 도는 설치라 앱만 systemd로 감싸도 DB가 없어 의미가 없다

### 측정값 (2026-08-09, t3.large 2 vCPU / 8GB)

| 항목 | 값 | 비교 |
|---|---|---|
| 맥 → EC2 TCP RTT | **10.0 ms** (중앙값, n=10) | 맥 → 로컬 VM 3.9 ms |
| OpenProxy(6432) 경유 | **앱 전 구간 동작** | 업로드·임베딩·검색 전부 |
| `pytest` 1회 (239 passed) | **32.1초** | 맥 로컬 컨테이너 15.9초 — **2.0배** |
| 프론트 `npm ci` / `build` | 23초 / 25초 | |
| 배포 스크립트 재실행 | 47.7초 | |

`pytest`는 **5432 직결**로 잰 것이다. OpenProxy 경유로는 돌지 않는다 — dbname 자리가 pool 이름이라 `conftest.py`의 `swap_dbname`이 존재하지 않는 풀을 가리킨다(해당 함수 주석 참조).

> **2.0배를 개발 환경 이전의 근거로 쓰지 마라.** #26이 VM에서 잰 11.9배에는 Apple Silicon 에뮬레이션이 섞여 있었고 EC2는 네이티브 x86-64라 격차가 작다. 그렇더라도 로컬 컨테이너가 여전히 두 배 빠르고, EC2는 확인 후 정지하는 자원이다 (ADR-026).

---

## 부록: 붙여넣기 주의

에뮬레이션 콘솔과 SSH 모두에서 겪은 문제다.

- **heredoc**(`<<'PY'`)은 붙여넣기 시 각 줄에 들여쓰기가 붙어 종료 마커를 인식하지 못한다
- **긴 한 줄 명령**은 터미널 폭에서 잘려 `&&` 뒤가 별도 명령으로 실행된다
- **줄 끝 백슬래시**로 이어 쓰면 공백이 누락돼 인자가 붙어버린다 (`192.168.64.4/24\` + `ipv4.gateway` → `24ipv4.gateway`)

**여러 줄 명령은 한 줄씩 나눠 실행한다.**
