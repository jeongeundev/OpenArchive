# OpenSQL 개발 환경 구축

> Tmax OpenSQL은 **x86-64 + Rocky Linux 9.7 전용**이다. Apple Silicon Mac에서는 가상머신이 필요하다.
> 설계 배경은 [ADR-007](ADR.md), [ADR-020](ADR.md). 이 문서는 **2026-08-05에 실제로 설치하며 검증한 절차**이며, 막혔던 지점을 ⚠️로 표시했다.

## 0. 구성 — DB만 가상머신에 둔다

> **현재 평가 환경은 HA 3노드(§16) + Barman 백업 노드 node4(§17)다.** §1~§11의 Single VM은 개발·OpenSQL 고유 동작
> 확인과 복원(PITR) 실습용으로 남아 있다. 1차 제출(2026-08) 때의 Single 구성 이야기는 §12 「1차 제출 시 구성」에 있다.

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
<end_date>2026/11/13</end_date>                         <!-- 만료일. 2026-09-14 재발급분 (초회 발급분은 2026/09/10) -->
```

`identified_by_host`를 VM hostname으로 그대로 쓴다. 틀리면 **PostgreSQL이 기동하지 않는다** — `patroni.yml`의 `shared_preload_libraries`에 `opensql_license`가 있어 검증이 DB 기동 시점에 일어난다.

`limit_cpu`는 **상한**이므로 4코어 이하면 된다. 소켓/코어/스레드 토폴로지를 정확히 맞출 필요는 없다.

### 라이선스 갱신 — 재설치는 필요 없다

라이선스는 설치 산출물이 아니라 **기동 시점에 읽는 파일 하나**다. 설치기는 XML을 `$OPENSQL_HOME/license/license.xml`(0640, `opensql:opensql`)로 복사하고 `.opensqlrc`에 `OPENSQL_LICENSE_PATH`를 적을 뿐이며(`opensql_local_installer.py`의 `deploy_license`), `start_patroni.sh`가 그 파일을 source해 `patroni.yml`의 `postgresql.env`로 넘긴다. `opensql_license`가 이 경로를 PostgreSQL 기동 시 읽는다. edition(Standard/Enterprise)은 설치 컴포넌트에 영향이 없다.

그러므로 재발급받으면 **파일 교체 + Patroni 재기동**으로 끝난다 (2026-09-15 실측):

```bash
# 맥 → VM. 인스톨러 사본에도 넣어 두면 재설치 때 그대로 쓴다
scp OpenSQL_Trial_opensql-dev_<만료일>.xml \
    kje@192.168.64.4:~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer/licenses/

# VM
sudo cp /home/opensql/license/license.xml /home/opensql/license/license.xml.expired-<옛만료일>
sudo install -m 640 -o opensql -g opensql \
    ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer/licenses/OpenSQL_Trial_opensql-dev_<만료일>.xml \
    /home/opensql/license/license.xml
sed -i 's|^LICENSE_NAME=.*|LICENSE_NAME="OpenSQL_Trial_opensql-dev_<만료일>.xml"|' \
    ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720/opensql-installer/config/common.env
```

이어서 §15 「인스턴스를 켤 때마다」의 기동 순서(Patroni → OpenProxy)로 띄우고 `patronictl list`가 `Leader · running`이면 통과다. PG 로그에 라이선스 메시지는 따로 찍히지 않는다 — preload 확장이라 서버가 뜬 것 자체가 검증이다.

교체 전에 새 XML의 `identified_by_host`가 VM hostname과 같은지, `limit_cpu`가 VM 코어 수 이상인지 확인한다. hostname이 다르면 `hostnamectl set-hostname`과 `/etc/hosts`를 맞춘다 — Patroni의 `NODE_NAME`은 hostname과 무관하므로 그것까지 바꿀 필요는 없다.

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
sed -i 's|^LICENSE_NAME=.*|LICENSE_NAME="OpenSQL_Trial_opensql-dev_20261113.xml"|' config/common.env

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

### 이미 쓰고 있는 OpenSQL에 설치할 때 — 새 DB와 새 풀

위 교정은 설치 직후의 빈 클러스터 이야기다. 이미 업무 데이터가 있는 OpenSQL이라면 **새 데이터베이스를
기본 경로로 삼는다** — `init`은 `documents`·`users` 같은 이름의 테이블이 이미 있으면 아무것도 바꾸지 않고
멈추기 때문이다(ADR-039). 앱 계정도 슈퍼유저가 아닌 별도 롤을 쓴다(2026-08-25 실측 경로).

**① DBA — 롤·DB·`vector` 확장.** `vector`는 신뢰 확장(trusted)이 아니라 슈퍼유저만 만들 수 있으므로
DBA가 **그 데이터베이스 안에서** 미리 만든다. `pg_trgm`은 미리 만들 필요가 없다 — 마이그레이션(005)이
없으면 만들고 있으면 넘어간다(2026-09-30 전에는 이미 있으면 `init`이 거부했다). 앱 롤이 DB 소유자라
`pg_trgm`(trusted)은 스스로 만든다.

```bash
psql -U postgres -c "CREATE ROLE openarchive LOGIN PASSWORD '<비밀번호>';"
psql -U postgres -c "CREATE DATABASE openarchive OWNER openarchive;"
psql -U postgres -d openarchive -c "CREATE EXTENSION vector;"
```

> 번들 `credcheck`가 롤 이름을 포함한 비밀번호를 거부한다(`password should not contain username`).

**② OpenProxy — 풀 추가.** 기존 풀 블록을 복사해 이름·계정·DB만 바꾼다. `servers`는 기존 풀과 같게 둔다.

```toml
[pools.openarchive]
pool_mode = "session"
default_role = "primary"
query_parser_enabled = false

[pools.openarchive.users.0]
username = "openarchive"
password = "<비밀번호>"
pool_size = 20

[pools.openarchive.shards.0]
servers = [
    ["<DB 호스트>", 5432, "primary"],     # 기존 풀의 servers 줄을 그대로 옮긴다
]
database = "openarchive"
```

풀 추가도 위 교정과 같은 이유로 `reload`가 아니라 **restart**를 쓴다(`bash $OPENSQL_HOME/scripts/restart_openproxy.sh`).
DSN의 데이터베이스 자리에는 풀 이름을 적는다 — `postgresql://openarchive:<비밀번호>@<OpenProxy 호스트>:6432/openarchive`.
HA 구성(etcd 공유 설정)이면 파일이 아니라 `openproxy edit` 또는 etcd 키로 바꾼다(§16).

**③ `pool_size`는 앱이 여는 연결 수보다 커야 한다.** `session` 모드에서는 클라이언트 연결 하나가 백엔드
하나를 끝까지 쥔다. 앱이 기동만으로 여는 연결은 다음과 같다(2026-09-29 로컬 실측, `pg_stat_activity`).

| 프로세스 | 연결 | 내역 |
|---|---|---|
| API | 4 | `psycopg_pool` 기본 크기 |
| 워커 | 5 | 풀 4 + LISTEN 전용 1 |
| MCP 서버 | 4 | 풀 기본 크기 — Claude 창 하나마다 한 프로세스 |
| 합계 | **13** | 여기에 `psql` 같은 관리 접속이 더해진다 |

설치기 기본값 `pool_size = 10`에서는 셋을 함께 띄우는 것만으로 모자라고, 넘친 클라이언트는
`couldn't get a connection after 30.00 sec`로 실패한다(2026-08-25 VM에서 10/10 소진 재현). 위 블록의
20은 MCP 창 하나와 관리 접속 몇 개를 더한 값이다. 백엔드 합계는 `max_connections`(100) 안에 들어가야 한다.
§16의 HA 구성처럼 `transaction` 모드면 트랜잭션 사이에 백엔드를 나눠 쓰므로 이 산식이 그대로 적용되지 않는다.

**④ 새 DB를 만들 수 없을 때 — 같은 DB 안 전용 스키마(`init --schema`).** 롤 이름과 같은 스키마에 설치하고
public은 건드리지 않는다. public에 `documents`·`users`가 있어도 된다. DBA가 할 일은 롤과 `vector`뿐이다.

```bash
psql -U postgres -c "CREATE ROLE openarchive LOGIN PASSWORD '<비밀번호>';"
psql -U postgres -c "GRANT CONNECT, CREATE ON DATABASE <기존 DB> TO openarchive;"   # CREATE = 스키마를 만들 권한
psql -U postgres -d <기존 DB> -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

풀은 새로 만들지 않고 그 DB를 바라보는 기존 풀에 `users` 항목을 하나 더한다(`[pools.<풀>.users.N]`). 그다음
`openarchive init --dsn "postgresql://openarchive:<비밀번호>@<OpenProxy 호스트>:6432/<풀>" --schema`.

- 스키마 이름은 **접속 롤 이름**이다. 앱은 따로 설정하지 않는다 — 기본 search_path `"$user", public`이
  그 스키마를 먼저 찾는다. `ALTER ROLE … SET search_path`나 DSN `options`로 스키마를 고르는 방식은
  OpenProxy를 거치면 동작하지 않는다(`OPENSQL_RESEARCH.md` §12-25). 같은 이유로 그 롤이나 DB에 search_path를
  따로 설정해 두면 `init`이 거부한다 — 판정은 접속한 백엔드의 값이 아니라 새 연결이 받을 롤·DB 설정으로 한다.
- 같은 롤로 **public에 이미 설치했다면** `--schema`는 거부한다. 스키마가 생기는 순간 모든 연결이 빈 전용
  스키마를 봐, 기존 문서가 에러 없이 보이지 않게 되기 때문이다.
- 롤에 DB CREATE를 줄 수 없으면 DBA가 스키마를 먼저 만든다(`CREATE SCHEMA openarchive AUTHORIZATION openarchive`).
  다만 `pg_trgm`이 그 DB에 없다면 만들 권한(DB CREATE)이 필요하므로 DBA가 `pg_trgm`도 미리 만든다.
- 확장이 DB에 없어 앱 롤이 직접 만들면 확장은 public이 아니라 그 스키마에 생긴다 — `vector`(슈퍼유저일 때)뿐
  아니라 `pg_trgm`(trusted라 DB CREATE만으로 만든다)도 그렇다. 확장은 DB에 하나뿐이라, 다른 롤이 쓰려면 그
  스키마의 USAGE가 필요해진다. 조직 DB라면 DBA가 둘 다 public에 미리 만들어 두는 편이 낫다.
- ⚠️ **Patroni 동기 복제를 켠 HA 구성에서는 풀 사용자를 추가할 수 없다.** OpenProxy 1.1.3이 Patroni
  `/cluster`의 `sync_standby` 역할을 해석하지 못해, 설정 재로드도 재기동도 실패한다(로그 `unknown variant
  sync_standby`). 이 저장소의 HA 환경은 그래서 2026-09-30 비동기로 되돌렸다(§16 5번, ADR-049 개정).

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

psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"               # ADR-016 — 관리용 postgres DB에서의 확인. 앱 DB에는 미리 만들지 않는다(§10)
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
| Failover | Single 구성에서는 원리적으로 불가 → ✅ 3노드에서 실측 (§16, ADR-020 2026-09-28 개정) |

---

## 12. 알려진 제약

| 제약 | 영향 |
|---|---|
| **x86-64 에뮬레이션** | 부팅·쿼리가 느리다. 애플리케이션을 맥 네이티브로 두는 이유 |
| **라이선스 hostname 고정** | `opensql-dev` 외의 hostname에서는 DB가 기동하지 않는다 |
| **라이선스 만료 2026/11/13** | 이후 DB 기동 불가. trial 기간이 짧다(초회 38일·재발급 60일). 만료되면 사무국에 재발급을 요청하고 §1 「라이선스 갱신」대로 파일만 교체한다 (ADR-021) |

### 1차 제출 시 구성

1차 제출 때는 사무국 지시로 **single 구성**이었고, 이 VM 하나로는 실제 failover를 시연할 수 없었다. 2차 평가에서
HA 라이선스(node1~3)를 다시 받아, 지금의 평가 환경은 **HA 3노드(§16) + Barman 백업 노드 node4(§17)**다. 이 절의 Single
VM은 개발·복원 실습용으로 남는다. 위 표의 라이선스 만료일은 이 Single VM의 것이고, HA 3노드 라이선스는 따로 만료된다(§16).

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

> **사용하지 않음 — 대회 규정상 EC2를 쓸 수 없다. 기록으로 보존한다.** §15도 같다.

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

- **이 인스턴스에 설치된 라이선스는 2026-09-10에 만료됐다.** 다시 켜려면 §1 「라이선스 갱신」대로 새 XML(만료 2026-11-13)로 교체해야 PostgreSQL이 기동한다 (ADR-021)
- **인스턴스를 정지했다 켜면 공인 IP가 바뀐다.** 심사용 링크를 유지하려면 Elastic IP나 도메인이 필요하다
  - 반면 **사설 IP는 보존된다**(실측: 정지·기동 후에도 `172.31.25.213`). `patroni.yml`·`openproxy.toml`이 사설 IP에 묶여 있으므로 재설정 없이 그대로 뜬다
- **재부팅하면 `opensql-etcd.service` 하나만 살아난다.** Patroni·PostgreSQL·OpenProxy는 systemd 유닛이 없는 `nohup` 맨 프로세스라(#27) 인스턴스를 켤 때마다 수동 기동이 필요하다 (§15)
- ⚠️ **`patronictl list`는 죽은 클러스터도 `Leader | running`으로 보여준다.** DCS(etcd)에 남은 마지막 기록을 출력하는 것이라, Patroni·PostgreSQL·OpenProxy가 전부 죽고 etcd만 살아 있는 상태(= 인스턴스를 켠 직후)에서도 정상처럼 보인다. **생사 판정에 쓰지 마라** — `deploy_app_host.sh`의 사전 검사가 이것 때문에 거짓 통과했다 (2026-08-19 실측, ADR-038)
  - 판정은 **앱이 쓸 DSN으로 쿼리 하나**를 던져서 한다: `sudo -u opensql env PGCONNECT_TIMEOUT=5 /home/opensql/bin/psql "$DATABASE_URL" -tAXc 'select pg_is_in_recovery()'` → `f`
  - 포트 리스닝(`ss -tln`)도 부족하다. OpenProxy는 커넥션 풀러라 백엔드 PostgreSQL이 죽어도 자기는 리스닝하고, 연결이 서더라도 Replica로 라우팅되면 마이그레이션이 read-only로 죽는다. 쿼리 한 번이 프로세스 생존·자격증명·풀 이름·쓰기 가능 여부를 함께 확인한다
  - `pkill -f openproxy` 같은 명령은 **자기 자신의 셸도 죽인다**(명령줄에 그 문자열이 들어 있으면 매치된다). 프로세스 이름만 보는 `pgrep -x`/`pkill -x`를 쓴다
- **CPU는 4개를 넘길 수 없다.** 라이선스 `<limit_cpu>` 상한이라 t3.xlarge(4 vCPU)가 최대다
- 설치 파일과 라이선스 xml은 **저장소에 커밋하지 않는다.** 저장소는 규정상 public이어야 한다 (대회 규정 제10조 ②)

---

## 15. 앱을 배포해 공개 URL로 띄우기

> **사용하지 않음 — 대회 규정상 EC2를 쓸 수 없다. 기록으로 보존한다** (§14와 같다).

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

이미지·스캔 PDF의 텍스트 인식(ADR-052)을 쓰려면 워커 호스트에 tesseract와 한국어 모델도 둔다. AppStream 패키지다. `deploy_app_host.sh`는 이것을 설치하지 않는다.

```bash
sudo dnf install -y tesseract tesseract-langpack-kor     # tesseract 4.1.1
tesseract --list-langs                                   # kor 가 있어야 한다
```

> Rocky 9 패키지(tesseract 4.1.1 + langpack-kor 4.1.0)는 `rockylinux:9` 컨테이너 실측에서 CER 0.031~0.050·쪽당 3.4~5.5초였다(#135 코멘트, arm64 컨테이너라 x86 호스트 시간과는 다를 수 있다). 맥 tesseract 5.5 실측(CER 0.068~0.093)보다 낮다.

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
- **느린 것은 첫 검색이 아니라 기동이다.** API·워커가 기동 시 모델을 예열하므로(ADR-003 보강) 첫 질의부터 **0.4~0.5초**다. 예전에는 lazy load라 재기동 후 첫 질의가 **16~19초**였고 시연 전에 질의를 한 번 흘려야 했는데, 그 대응은 더 이상 필요 없다. 대신 **기동이 그만큼 걸린다** — 가중치가 캐시된 호스트에서 12~13초, 캐시가 없으면 내려받는 시간이 더해진다. `deploy_app_host.sh`의 헬스체크 여유(300초)가 이 때문이다
- **재부팅 생존은 없다.** API·프론트는 `nohup` 맨 프로세스다. OpenSQL 자신이 그렇게 도는 설치라 앱만 부팅 시 살려도 붙을 DB가 없다. 인스턴스를 켤 때마다 `deploy_app_host.sh`를 돌린다
- **임베딩 워커만 systemd 유닛이다 — 재부팅이 아니라 crash 복구용이다** (ADR-038). DB는 살아 있는데 워커만 죽는 경우가 문제이기 때문이다: 화면은 멀쩡한데 새 문서만 영원히 검색되지 않는다. BGE-M3가 2.2GB 상주하고 swap이 없어 워커는 OOM killer의 1순위 표적이다
  - **system 유닛이 아니라 user 유닛이다.** SELinux Enforcing에서 system 유닛(`init_t`)은 홈 아래(`user_home_t`)의 venv를 실행하지 못한다 — `203/EXEC Permission denied`. `SELinuxContext=`로 exec 도메인만 바꿔도 경로 탐색에서 걸린다. `systemctl --user`는 nohup으로 돌리던 때와 같은 도메인이라 보안 수준이 낮아지지 않는다. 세션이 끊겨도 유지되도록 `loginctl enable-linger`가 필요하고 `deploy_app_host.sh`가 해 둔다 (2026-08-19 실측)
  - 상태·로그: `systemctl --user is-active openarchive-worker` · `journalctl --user -u openarchive-worker -f` (**sudo 불필요**)
  - 세우고 켜기: `systemctl --user stop|restart openarchive-worker` (`pkill`을 쓰지 않는다 — `Restart=always`가 즉시 되살린다)
  - **`systemctl --user enable`은 일부러 실패한다.** 유닛에 `[Install]` 섹션이 없어 부팅 자동 기동을 켤 수 없고 `is-enabled`가 `static`으로 남는다. 위 "재부팅 생존은 없다"를 파일 구조로 강제한 것이다
  - 5분에 5회를 넘겨 재기동하면 systemd가 포기하고 failed로 남는다. 다시 띄우려면 `systemctl --user reset-failed openarchive-worker`가 필요하다. 그 상태에서도 업로드는 계속 받으므로 `/admin/status`의 `pending`이 계속 올라 정지 사실은 화면에서 보인다 (`recovery_pending`은 크래시 시점에 방치된 `processing` 건수라 더 늘지 않는다)
  - `XDG_RUNTIME_DIR`이 없는 비대화형 SSH에서는 `export XDG_RUNTIME_DIR=/run/user/$(id -u)`를 먼저 준다

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

## 16. HA 3노드 구성 (#110)

2차 평가를 위해 HA 라이선스(node1~3, 만료 **2026-10-28**)를 받아 VM 3대에 구축했다 (2026-09-25~26 실측). **설치기가 끝난 상태는 공식 HA 구성이 아니다** — 아래 「설치 후 교정」을 전부 해야 앱을 받을 수 있다. 교정마다 OpenSQL 문서·배포판과 어디가 왜 다른지는 [`OPENSQL_DEVIATIONS.md`](OPENSQL_DEVIATIONS.md)에 모았다.

```
                     VIP 192.168.64.200:6432  (VRRP, node2 MASTER · node3 BACKUP)
                                  │
             ┌────────────────────┴────────────────────┐
     node2 OpenProxy                            node3 OpenProxy
             └──────────── etcd에서 leader 감시 ────────┘
                                  │  쓰기·BEGIN → primary / 트랜잭션 밖 SELECT → replica
   node1 (.201)                node2 (.202)                node3 (.203)
   PostgreSQL Leader    ──►    Replica             ──►     Replica
                               (둘 다 async — 교정 5)
   Patroni · etcd              Patroni · etcd              Patroni · etcd
```

### VM 준비

- 베이스 VM 하나를 §2~§6대로 만들고(4GB · 4코어 · 64GB — 라이선스에 신고한 1 CPU × 4 core와 맞춘다) `utmctl clone`으로 3대를 뜬다
- 베이스에 추가로 넣는 것: 방화벽 `5432,6432,6433,2379,2380,8008/tcp` + `--add-protocol=vrrp`, `/etc/hosts`에 node1~3
- ⚠️ 클론마다 **machine-id·SSH 호스트 키를 재생성**하고 **MAC을 UTM에서 새로 만든다**(같은 MAC이면 DHCP·ARP가 섞인다). 그 뒤 hostname `node1~3`, 고정 IP `192.168.64.201~203`
- 라이선스는 노드마다 다르다(hostname에 묶임). 설치기 `config/remote.env`의 `NODE{n}_LICENSE_NAME`에 각각 지정한다

### 설치

node1에서 원격 설치기를 돌린다. `config/common.env`에 `NODE{1,2,3}_IP`, `config/remote.env`에 노드 이름·SSH 사용자·라이선스를 채운다.

```bash
python3 opensql_remote_installer.py --mode 3node
```

- 약 1시간 20분 걸린다(대부분 pgvectorscale Rust 빌드, 에뮬레이션)
- SSH 비밀번호를 `getpass`로 묻는다. NOPASSWD sudo면 빈 줄로 넘긴다
- 결과: Patroni Leader(node1) + Replica 2(streaming, **async**), etcd 3노드, **OpenProxy는 node2·node3에만**

### ⚠️ 설치 후 교정 (필수)

설치기 기본 OpenProxy(`session` · parser off · `default_role=primary`)는 **세션을 역할과 무관하게 세 서버에 무작위로 보낸다.** VIP 경유 쓰기 10건 중 6~8건이 "읽기 전용 트랜잭션" 오류로 실패했다. 설치기 출력은 공식 HA 구성이 아니다 (OPENSQL_RESEARCH §4).

1. **풀 DB 교정** — §10 「풀이 바라보는 데이터베이스」와 같다 (`database = "opensql"`)
2. **VIP 추가** — 설치기는 `owldb` 모드에서만 `[general.virtual_router]`를 만든다. node2·node3의 `openproxy.toml` `[general]` 뒤에 수동으로 넣고 `setcap cap_net_admin,cap_net_raw+ep /home/opensql/bin/openproxy`

   ```toml
   [general.virtual_router]
   interface = "enp0s1"
   router_id = 50
   priority = 100                         # node3는 90
   advert_int = 3
   vip_addresses = ["192.168.64.200/24"]
   unicast_peers = ["192.168.64.203"]     # node3는 .202
   ```
3. **공식 HA 구성으로** — 두 노드 모두 아래로 바꾸고 재시작한다(`etcd.enabled` 변경은 reload로 반영되지 않는다)

   ```toml
   [general.etcd]
   enabled = true
   endpoints = ["192.168.64.201:2379", "192.168.64.202:2379", "192.168.64.203:2379"]
   patroni_namespace = "/service"          # Patroni 키가 /service/opensql/… 에 있다
   patroni_scope = "opensql"

   [pools.opensql]
   pool_mode = "transaction"
   auth_type = "scram-sha-256"
   query_parser_enabled = true
   query_parser_read_write_splitting = true
   # default_role 줄은 지운다 (공식 예시에 없고 효과도 없었다)
   ```
   - `[general]`에 `worker_threads = 2` — HA 템플릿 권고(DB와 같은 노드면 코어 수의 절반, VM 4코어). 반영은 재시작
   - `auth_type`을 지정하지 않으면 OpenProxy 기본 `md5`다. HA 템플릿은 `scram-sha-256`이다(2026-09-30 맞춤, #150)
   - 먼저 뜬 노드가 자기 파일을 etcd `/service/opensql/openproxy/config/current`에 **초기 설정으로 올리고**, 이후 노드는 로컬 파일을 무시한다. 그래서 두 파일은 `virtual_router`의 `priority`·`unicast_peers`만 다르게 둔다
   - etcd 모드에서는 **파일 autoreload가 꺼진다.** 공유 설정 변경은 `openproxy edit` 또는 etcd 키로 한다
   - 역할 감지가 30초 폴링(`Patroni role refresh … 30 second interval`)에서 **etcd leader·members watch**로 바뀐다
   - 되돌리기: 백업 파일 복원 + 위 etcd 키 삭제 + 재시작
   - ❌ `session` + splitting은 쓰지 마라. 첫 쿼리로 서버가 고정돼 **한 연결에서 SELECT 후 쓰기가 10/10 실패**했다(로드밸런싱 문서의 경고와 일치)
4. **systemd 등록** — 설치기 기본(`ENABLE_SERVICE=etcd`)은 etcd만 유닛으로 만든다. Patroni·OpenProxy가 `nohup` 맨 프로세스라 **재부팅한 노드가 클러스터에 돌아오지 않는다**
   - 설치기 템플릿 `config/patroni.service`(3노드)·`config/openproxy.service`(node2·3)의 `$변수`를 실행 중인 Patroni 환경(`/proc/<pid>/environ`)으로 채워 `/etc/systemd/system/`에 두고 `enable`한다. Patroni 유닛에는 복제 비밀번호가 들어가므로 `600`
   - 맨 프로세스에서 넘길 때는 **`patronictl pause`** 후 노드별로 옛 Patroni에 SIGTERM → `systemctl start patroni` → `resume`. pause 중에는 Patroni를 멈춰도 PostgreSQL이 살아 있어(PID 불변) failover가 나지 않는다
   - ⚠️ OpenProxy에는 드롭인을 추가한다. 설치기 템플릿은 `After=network.target`뿐인데 공식 문서 예시는 etcd 뒤에 뜬다. etcd는 `Type=simple`이라 "Started"가 포트 준비를 뜻하지 않고, OpenProxy는 아직 안 열린 **로컬 etcd에 한 번 시도하고 로컬 파일로 대체**해 뜬다(재부팅 실측 2회). 대체된 노드는 이후 etcd 설정 변경을 놓친다

   ```ini
   # /etc/systemd/system/openproxy.service.d/10-after-etcd.conf
   [Unit]
   After=network-online.target opensql-etcd.service
   Wants=network-online.target

   [Service]
   ExecStartPre=/bin/sh -c "for i in $(seq 30); do /home/opensql/bin/etcdctl --endpoints=http://127.0.0.1:2379 --dial-timeout=1s endpoint health >/dev/null 2>&1 && exit 0; sleep 1; done; exit 0"
   ```
   - 로그: OpenProxy는 journald와 `/home/opensql/logs/<날짜>.openproxy.log`, Patroni는 `/home/opensql/logs/patroni.log`
5. **복제는 비동기 그대로 둔다 (ADR-049 개정)** — 설치기 기본이자 공식 문서 예시가 비동기다. failover 때
   커밋 응답을 받은 데이터를 잃을 수 있다. `maximum_lag_on_failover`(1MB)는 마지막으로 보고된 지연이 그보다
   큰 replica를 후보에서 빼 줄 뿐 유실 상한을 보장하지 않는다(보고 주기 뒤의 WAL은 세지 않고, 시간 기준도 아니다 — #165).
   - ⛔ **OpenProxy 1.1.3에서는 동기 모드(`synchronous_mode: true`)를 켜지 않는다.** Patroni `/cluster`에
     `sync_standby` 역할이 생기면 OpenProxy가 기동·설정 재로드 때 `unknown variant sync_standby` →
     `Config parse error`로 종료한다(exit 78). 떠 있는 프로세스는 버티지만 **재시작하는 순간 죽는다.** 9/27에
     켰다가 9/30에 node2가 이 상태로 발견돼 되돌렸다(#148)
   - 켜져 있다면 끈다(재시작 없음). 그 뒤 OpenProxy는 자동 재시작 루프에서 바로 뜬다

     ```bash
     sudo -u opensql /home/opensql/bin/patronictl -c /home/opensql/etc/patroni/patroni.yml edit-config \
       -s synchronous_mode=false --force
     ```
   - 확인: `patronictl list`에 `Sync Standby`가 없고, Primary의 `pg_stat_replication.sync_state`가 모두 `async`
   - 참고(동기 모드를 쓰는 OpenProxy 수정판이 나왔을 때): 동기 모드에서는 **switchover 후보가 `sync_standby`여야 한다.** 다른 replica를 고르면 `412, candidate name does not match with sync_standby`로 거부된다. 특정 노드로 리더를 옮기려면 그 노드가 `sync_standby`가 될 때까지 "현재 sync_standby로 switchover"를 반복한다
6. **서버 keepalive (ADR-051)** — 기본값(keepalive 7200초)이면 **죽은 OpenProxy 노드를 거치던 트랜잭션이 Primary에 약 2시간 락을 쥔 채 남는다**(#122 S5a-2 — 워커 13분 넘게 정지). 앱 쪽 keepalive(ADR-048)와 같은 값으로 맞춘다. **OpenSQL 공식 구성에 없는 설정이다** — OpenProxy가 죽은 경우를 막을 공식 대안이 없어서 더했다([`OPENSQL_DEVIATIONS.md`](OPENSQL_DEVIATIONS.md) 1절)

   ```bash
   sudo -u opensql /home/opensql/bin/patronictl -c /home/opensql/etc/patroni/patroni.yml edit-config \
     -p tcp_keepalives_idle=30 -p tcp_keepalives_interval=10 -p tcp_keepalives_count=3 \
     -p tcp_user_timeout=60000 --force
   ```
   - 재시작 없이 세 노드에 반영되고, 이미 열린 OpenProxy 풀 연결에도 적용된다(`ss -tno`의 keepalive 타이머가 30초 이하로 바뀐다)
   - `idle_in_transaction_session_timeout`은 걸지 않는다. 9/28~9/30에 60s를 클러스터 전체에 걸었으나, 같은 DB의 다른 앱 세션까지 끊고 위 keepalive로 충분해 걷었다(#150, ADR-051 개정). 켜져 있다면 `-s postgresql.parameters.idle_in_transaction_session_timeout=null`
7. **앱 전용 롤·DB·풀** — 설치기 풀 사용자는 `postgres` 슈퍼유저 하나다. 앱은 HA 템플릿처럼 전용 롤로 붙는다. 롤·DB·`vector`는 §10 「새 DB와 새 풀」 ①과 같고, 풀은 HA 형태로 공유 설정에 더한다(`openproxy edit`, 편집 뒤 두 노드 재시작)

   ```toml
   [pools.openarchive]
   pool_mode = "transaction"
   auth_type = "scram-sha-256"
   query_parser_enabled = true
   query_parser_read_write_splitting = true

   [pools.openarchive.users.0]
   username = "openarchive"
   password = "<비밀번호>"
   server_username = "openarchive"
   server_password = "<비밀번호>"
   pool_size = 20
   statement_timeout = 0

   [pools.openarchive.shards.0]
   servers = [
       ["192.168.64.201", 5432, "Auto"],
       ["192.168.64.202", 5432, "Auto"],
       ["192.168.64.203", 5432, "Auto"],
   ]
   database = "openarchive"
   use_patroni = true
   patroni_port = "8008"
   ```
   - 그다음 `openarchive init --dsn "postgresql://openarchive:<비밀번호>@192.168.64.200:6432/openarchive"`. 2026-09-30 실측: 비슈퍼유저로 23개 마이그레이션 적용·관리자 생성, 업로드→임베딩→검색, switchover 뒤 재시작 없이 업로드 3건·검색 정상. 틀린 비밀번호는 `invalid client proof`로 거부
   - `postgres` 풀은 운영 접속(`psql`, 측정 도구의 노드 대조)용으로 남긴다

### 검증 (2026-09-26 실측)

VIP·node2·node3 직접 모두 같은 결과다.

| 확인 | 결과 |
|---|---|
| 트랜잭션 밖 SELECT | 20/20 replica (두 replica에 분산) |
| `BEGIN` 안 SELECT / `BEGIN READ ONLY` 안 SELECT | 10/10 primary / 10/10 replica |
| 한 연결에서 SELECT 후 CREATE·INSERT | 오류 0 |
| 트랜잭션 밖 INSERT | 10/10 primary 반영 |
| 트랜잭션 밖 `SELECT txid_current()` | **10/10 실패** — 쓰기 함수를 SELECT로 부르면 replica로 간다(공식 문서 경고). `BEGIN`으로 감싸면 성공 |
| OpenProxy `kill -9` | 1초 뒤 재기동 |
| 실제 Patroni 프로세스 `kill -9` | 1회 재기동, PostgreSQL PID 유지 |
| node3 재부팅 | etcd·Patroni·OpenProxy 자동 기동(NRestarts 0), replica 재합류, OpenProxy가 etcd 설정 로드 |

- ⚠️ `/home/opensql/bin/patroni`는 **PyInstaller 실행 파일이라 프로세스가 둘**이다(부트로더 → 실제 Patroni). systemd의 MainPID는 부트로더다. 부트로더에 `kill -9`를 보내면 실제 Patroni가 고아로 남아 8008을 쥔 채 클러스터를 계속 관리하고, 새 Patroni는 8초마다 죽는 루프에 빠진다(`KillMode=process`라 systemd가 고아를 정리하지 않는다). 장애 주입은 **자식(`pgrep -P <MainPID> -x patroni`)에** 한다. 고아는 SIGTERM으로 정리한다

### 최종 구성 장애 검증 (#165, 2026-10-04) — 현재 수치의 정본

**대상 구성**: 이 절 위 「설치 후 교정」을 모두 적용한 상태 — 비동기 복제(교정 5)·`failsafe_mode: true`(설치기 기본)·서버 keepalive(교정 6)·`idle_in_transaction_session_timeout` 0(공식값)·앱 전용 비슈퍼유저 롤 `openarchive` + `ScramSha256`·OpenProxy etcd 공유 설정(`Transaction` 풀·읽기/쓰기 분리·`use_patroni`·`worker_threads 2`)·watchdog 꺼짐. 측정 직후 `patronictl show-config`·etcd의 OpenProxy 설정·`pg_settings`로 위 값을 다시 확인했다. 앱은 main `0cb629b`(10/3 회차는 `a00f983`, 9/30 회차는 #150 적용 직후). 세 시점 사이에 워커·`db.py`를 포함한 앱 코드가 바뀌었으므로(#153~#171) 9/30·10/3 회차는 같은 클러스터 구성의 보조 증거로만 읽는다 — 모든 시나리오를 `0cb629b`로 다시 잰 것이 h165 회차다. 임베딩은 BGE-M3(`EMBEDDING_PROVIDER=local`), 측정 중 Mac의 다른 도커 컨테이너는 껐다(9/30 회차만 켜져 있었다).

**판정**(`scripts/ha_failover.py`, 위반 하나면 종료 코드 1): 업로드는 텍스트 API와 **파일 업로드를 번갈아** 보낸다. 커밋 응답(2xx)을 받은 업로드가 전부 있고 본문 sha256이 같은가(유실 0) · 파일은 **원본 판(`document_files`)이 1판 하나로 보낸 바이트와 같은가**(DB 계산 sha256·재계산 둘 다) · 문서 버전이 1에 머물렀는가 · 실패 응답인데 남은 행·중복 행이 없는가 · 백오프(웹 UI·MCP와 같은 규칙) 뒤 사용자 가시 실패 0 · 원시 500 0 · error 잡 0 · 정합성 카운터 0 수렴 · 3노드 5432 직결 다이제스트(본문·청크 수·원본 바이트) 일치.

| 시나리오 | 회차 | 쓰기 중단 | 재시도로 넘긴 요청 (재시도 포함 대기) | 업로드 2xx (그중 파일) | 유실·중복·원본 불일치·버전 이동 | 0 수렴 | 판정 |
|---|---|---|---|---|---|---|---|
| 무장애 60초 | h165-smoke | 0 | — | 120 (60) | 0·0·0·0 | 2.2s | 통과 |
| Primary postmaster `kill -9` (node3) | h165-s1 | 14.1s — 같은 노드에서 재기동, TL 그대로 | 업로드 14.3s·검색 21.6s (503 3~4회) | 272 (136) | 0·0·0·0 | 18.3s | 통과 |
| switchover node3→node1 | h165-sw | 10.3s | 검색 12.1s (503) | 220 (110) | 0·0·0·0 | 5.3s | 통과 |
| 〃 | fspec-switchover (10/3) | 10.1s | 검색 11.7s (503) | 220 (—) | 0·0·—·— | 8.2s | 통과 |
| Primary VM 전원 차단 (node3 = Primary + 대기 OpenProxy) → 90초 뒤 기동 | h165-s2 | 40.8s | 업로드 41.3s·검색 43.1s (타임아웃 2회) | 398 (199) | 0·0·0·0 | 53.0s | 통과 |
| Primary VM 전원 차단 (node1, OpenProxy 없음) | fspec-primary-loss (10/3) | 30.6s | 업로드 31.0s·검색 31.9s | 417 (—) | 0·0·—·— | 63.3s | 통과 |
| Replica VM 전원 차단 (node1) → 60초 뒤 기동 | h165-s5 | 0 | — | 379 (189) | 0·0·0·0 | 22.3s | 통과 |
| VIP MASTER VM 전원 차단 (node2, Replica) → 60초 뒤 기동 | h165-s3 | 10.2s (VIP 이동) | 검색 16.1s — VIP **선점 복귀**(+131.7s) 순간 | 453 (226) | 0·0·0·0 | 11.6s | 통과 |
| 〃 (idle_in_transaction 걷은 뒤 첫 회차) | s3-150-1 (9/30) | 10.2s + 0.3s(선점 복귀) | 검색 16.0s — 선점 복귀 순간 | 460 (—) | 0·0·—·— | 28.5s | 통과 |
| Leader 정상 재부팅 (`systemctl reboot`, node1) | h165-reboot | 10.3s — node3 승격, node1은 +104s에 systemd로 자동 재합류 | 업로드 11.6s·검색 13.1s (503) | 457 (228) | 0·0·0·0 | 10.4s | 통과 |
| etcd 1대 정지 40초 (Leader 노드 node3) | h165-s4 | 0 | — | 239 (119) | 0·0·0·0 | 77.4s* | 통과 |
| **etcd 과반 상실** (node1·node2 정지 60초) | h165-etcdq | 0 — `failsafe_mode`가 Primary 유지 | — | 358 (179) | 0·0·0·0 | 258.8s* | 통과 |
| **Leader 네트워크 분리** (node1을 node2·3에서 90초 차단) | h165-part | 30.7s — node3 승격 | 업로드 25.9s·검색 26.8s | 429 (214) | 0·0·0·0 | 6.9s | 통과 |

\* 수렴 시간은 회차끼리 비교하지 않는다. 측정 문서가 16단어 어휘의 거의 같은 글이라 회차가 쌓일수록(마지막 회차 직전 3,325건·관계 16,945행) 관계 재계산이 느려졌다 — smoke 2.2s → etcdq 258.8s. 판정(0 수렴)에는 영향이 없다. 측정이 끝난 뒤 `ha-h165-*` 문서를 지우고 `openarchive rebuild-edges`로 시연 데이터의 관계를 다시 맞췄다.

**쓰기 중단·검색 불가·사용자 실패를 구분해 읽는다.** "쓰기 중단"은 VIP로 100ms마다 WAL을 남기는 probe의 마지막 성공~첫 성공이다(실패 구간마다 따로, #151). "재시도로 넘긴 요청"은 그 구간에 걸린 업로드·검색 하나가 백오프 재시도 끝에 성공하기까지 걸린 시간 — 순차 부하라 사실상 **그 동안 검색·업로드가 안 됐던 시간**이다. 사용자 가시 실패(백오프 60초 예산을 다 쓴 요청)는 전 회차 0이다. **재시도가 실패를 흡수한 것이지 중단이 없었던 것이 아니다** — "무중단"이라 쓰지 않는다.

**관측 유실 0은 RPO 0이 아니다.** 비동기 복제라 Primary가 죽는 순간 replica에 닿지 않은 커밋은 잃을 수 있다. `maximum_lag_on_failover`(1MB)는 **마지막으로 보고된** 지연이 1MB를 넘는 replica를 후보에서 빼는 장치일 뿐이다 — 보고는 Patroni 루프 주기(`loop_wait` 10초)마다라 마지막 보고 뒤의 WAL은 세지 않고, 바이트 기준이라 시간 상한도 아니다. 아래 분리 회차가 실제로 응답한 커밋이 버려지는 경로를 보여 준다.

**네트워크 분리 (h165-part) — Patroni의 자기 강등이 펜싱 역할을 했다.**

| 분리 기준 시각 | 일어난 일 |
|---|---|
| +4.9s | node1(Leader)에 nft 차단 적용. OpenProxy(node2·3)가 node1에 못 닿아 VIP 경유 쓰기는 여기서 멈춘다 |
| +4.9 ~ +18.9s | node1은 **여전히 Primary** — Mac에서 5432로 **직접** 붙은 쓰기 19건을 받았다 |
| +19.8s | `demoting self because DCS is not accessible and I was a leader` — etcd도, `failsafe`로 물어볼 멤버도 닿지 않음 |
| +20 ~ +70s | 강등(fast shutdown)이 끊긴 replica로 가는 walsender를 기다려 약 50초 걸렸다(서버 keepalive가 끊어 줌). 그동안 접속 거부 |
| +35.5s | node3 승격(TL 40). **두 Primary가 동시에 쓰기를 받은 구간은 없었다** |
| +90s / +173s | 차단 해제 → node1이 `pg_rewind from postgresql3`로 되감겨 Replica 재합류. **직접 쓰기 19건은 이때 버려졌다** |

- 앱 쓰기는 OpenProxy만 거치므로(ADR-006) 분리된 옛 Primary에 닿지 못했고 장부 유실 0이었다. OpenProxy를 우회해 옛 Primary에 직결한 클라이언트는 응답받은 커밋을 잃는다 — 비동기 + 우회의 결과이며 단일 엔드포인트 규칙의 근거가 하나 더 생긴 셈이다
- **watchdog은 켜지 않았다**(공식 문서·설치기 모두 설정하지 않음 — `OPENSQL_DEVIATIONS.md` 원칙). 위 강등은 Patroni 프로세스가 살아 있어야 일어난다. **Patroni가 멈춘 채 PostgreSQL만 Primary로 남는 경우의 펜싱은 검증하지 않았다**

**etcd 과반 상실 (h165-etcdq)**: node3 Patroni가 `Error communicating with DCS` 뒤 `continue to run as a leader because failsafe mode is enabled and all members are accessible`를 남기고 Primary를 지켰다. etcd 감시 모드의 OpenProxy도 기존 역할로 계속 라우팅해 쓰기 중단·실패 0. 이 구간에는 **새 failover가 일어날 수 없다** — 이 상태에서 Primary까지 죽으면 etcd가 돌아올 때까지 쓰기가 멈춘다(복합 장애, 검증하지 않음).

**재현 명령** — 저장소 루트에서, API·워커(`openarchive serve`)를 VIP DSN으로 띄우고 측정 계정을 만든 뒤:

```bash
export DATABASE_URL=postgresql://openarchive:…@192.168.64.200:6432/openarchive HA_API=http://127.0.0.1:8010/api HA_USER=… HA_PASSWORD=…
H="--nodes 192.168.64.201,192.168.64.202,192.168.64.203 --out <결과 폴더>"
SSH="ssh -F notes/ha110/ssh_config"; U=/Applications/UTM.app/Contents/MacOS/utmctl
P=/home/opensql/bin/patronictl; C=/home/opensql/etc/patroni/patroni.yml
python scripts/ha_failover.py run s1 $H --load 150 --inject "$SSH <Leader> 'sudo kill -9 \$(sudo head -1 /home/opensql/data/pgsql/postmaster.pid)'"
python scripts/ha_failover.py run sw $H --load 120 --inject "$SSH <Leader> 'sudo -iu opensql $P -c $C switchover --leader <현> --candidate <새> --force'"
python scripts/ha_failover.py run s2 $H --load 240 --inject "$U stop <Leader VM> --force; sleep 90; $U start <Leader VM>"
python scripts/ha_failover.py run s5 $H --load 200 --inject "$U stop <Replica VM> --force; sleep 60; $U start <Replica VM>"
python scripts/ha_failover.py run s3 $H --load 240 --inject "$U stop <VIP MASTER VM> --force; sleep 60; $U start <VIP MASTER VM>"
python scripts/ha_failover.py run reboot $H --load 240 --inject "$SSH <Leader> 'sudo systemctl reboot' || true"
python scripts/ha_failover.py run s4 $H --load 120 --inject "$SSH <노드> 'sudo systemctl stop opensql-etcd; sleep 40; sudo systemctl start opensql-etcd'"
# 과반 상실·분리는 되돌리는 타이머를 노드 안에 먼저 건다 — ssh가 끊겨도 풀린다
python scripts/ha_failover.py run etcdq $H --load 180 --inject "for h in <노드A> <노드B>; do $SSH \$h 'sudo systemd-run --on-active=60 /usr/bin/systemctl start opensql-etcd && sudo systemctl stop opensql-etcd' & done; wait"
python scripts/ha_failover.py run part $H --load 240 --inject "$SSH <Leader> 'sudo systemd-run --on-active=90 /usr/sbin/nft delete table inet ha165 && printf \"table inet ha165 { chain i { type filter hook input priority -10; ip saddr { <다른 노드 둘> } drop; } chain o { type filter hook output priority -10; ip daddr { <다른 노드 둘> } drop; } }\" | sudo nft -f -'"
```

- 분리 회차는 OpenProxy가 없는 노드를 Leader로 둔 상태에서 했다. OpenProxy 노드를 떼면 양쪽 OpenProxy가 서로 못 들어 **VIP를 둘 다 잡을 수 있다** — 이 비대칭 분리는 검증하지 않았다
- 앱 전용 롤은 `pg_stat_replication`을 볼 수 없어 측정기의 「복제 지연」 줄이 0으로 찍힌다. 지연은 Patroni REST(`/cluster`의 `lag`)로 본다
- 원시 기록은 `notes/ha110/runs/h165-*.jsonl`·`fspec-*.jsonl`·`s3-150-1.jsonl`(로컬). 측정 문서를 지운 뒤라 `report`로 장부를 다시 대조할 수는 없다 — 위 장부 열은 측정 당시 출력이다

**검증하지 않은 것**: watchdog 펜싱 · Patroni 프로세스 정지 상태의 Primary · OpenProxy 노드가 낀 비대칭 분리(VIP 이중 보유) · 동시 다중 장애(etcd 과반 상실 중 Primary 사망 등) · 디스크 가득 참 · 백업/DR(§17 — 분리 회차 포함). 각 시나리오는 1회씩이라 시간 수치는 범위가 아니라 관측값이다.

### 장애 주입 측정 (#122, 2026-09-28) — 동기 복제 시절 기록

> 아래는 **동기 1대 구성**에서 잰 기록이다. 지금 구성의 수치는 위 #165 절이 정본이다.

`scripts/ha_failover.py`가 부하(업로드 약 2건/초·검색·100ms 쓰기 probe)를 건 채 장애 명령을 실행하고, 수렴을 기다려 판정한다. 판정 항목은 장부 대조(커밋 응답을 받은 업로드의 존재·sha256, 실패 응답인데 DB에 남은 행, 중복), 백오프 뒤 사용자 가시 실패, 원시 500, 정합성 카운터 0 수렴, 3노드 5432 직결 다이제스트 일치, 승격 대상이 동기 standby였는지다. 위반이 하나라도 있으면 종료 코드 1이다.

```bash
# API·워커를 VIP에 붙여 띄운 뒤 (계정은 미리 만든다)
DATABASE_URL=postgresql://…@192.168.64.200:6432/opensql HA_PASSWORD=… \
  backend/.venv/bin/python scripts/ha_failover.py run s2-1 --out <결과 폴더> \
    --nodes 192.168.64.201,192.168.64.202,192.168.64.203 --load 240 --inject-at 40 \
    --inject 'utmctl stop node1; sleep 90; utmctl start node1'
# 기록(JSONL)으로 다시 판정
… scripts/ha_failover.py report s2-1 --out <결과 폴더> --nodes …
```

- ⚠️ **측정 전에 QEMU 프로세스 우선순위를 확인한다.** `ps -axo pri,command | grep QEMULauncher`가 `4`(macOS 백그라운드)면 그 VM은 5~6배 느리다. 9/27 측정 중 다시 켠 node1이 이 상태로 하루 동안 돌며 워커 처리량 저하·etcd 지연 경고·무부하 자동 failover의 원인이 됐다. `taskpolicy -B`로는 풀리지 않았고, 리더를 옮긴 뒤 VM을 정상 종료하고 다시 켜서(`31`) 풀었다. 어떤 경로로 백그라운드 우선순위가 붙었는지는 확정하지 못했다
- 아래 표는 **동기 1대 구성**(9/27~9/30)에서 쟀다. 지금 구성은 비동기다(「설치 후 교정」 5, ADR-049 개정). 동기 모드에서 리더를 되돌리는 법도 교정 5의 참고를 본다. 전원을 끊은 VM이 다시 VIP를 되찾기까지는 부팅 시간(1~4분)이 걸리므로, 되돌린 뒤 VIP가 node2에 있는지 확인하고 다음 회차를 시작한다

| 시나리오 (동기 1대, 표시 없으면 교정 6 적용 후) | 쓰기 중단 | 승격 | 사용자 가시 실패 | 원시 500 | 유실 | 수렴(부하 종료 뒤) |
|---|---|---|---|---|---|---|
| 무장애 300초 | 0 | — | 0 | 0 | 0 | 1.6s |
| S2 리더 전원 차단 ×3 (교정 6 전) | 30.7~40.7s | 동기 standby로 3/3 | 0 | 0 | 0 | 4.0s 이내 |
| S5a 동기 standby 전원 차단 (교정 6 후 1회, 전 2회는 아래) | 24.5s(커밋 대기) | — | 0 | 0 | 0 | 3.0s |
| S5b replica 2대 Patroni 정지 (교정 6 전) | 0(비동기 강등) | — | 0 | 0 | 0 | 5.5s |
| S3 VIP MASTER 전원 차단 ×2 | 8.2~31.1s | — | 0 | 0 | 0 | 4.5s 이내 |
| S1 Primary postmaster `kill -9` | 11.4s | 없음 | 0 | 0 | 0 | 3.4s |
| S4a/b etcd 팔로워·리더 정지 | 0 | — | 0 | 0 | 0 | 4.4s 이내 |
| switchover ×3 | 10.3s(3회 모두) | 동기 standby로 3/3 | 0 | 0 | 0 | 3.3~29.4s |

- 교정 6 전의 S5a 2회는 커밋 대기가 38~39초였고, 그중 1회(동기 standby = VIP MASTER)는 죽은 OpenProxy 노드의 트랜잭션이 Primary에 락을 쥔 채 남아 **워커가 13분 넘게 멈춰 판정 위반**이었다 — 교정 6의 근거(ADR-051). 적용 후 같은 복합 조건이 두 번(S5a·S3 31.1초 회차) 재현됐고 모두 통과했다
- S3의 31.1초 회차는 node2가 VIP MASTER이면서 동기 standby였다(커밋 대기가 겹침). VIP만 옮긴 회차는 8.2초
- 백오프가 흡수한 503은 판정 밖에서 따로 남긴다(`report` 출력의 「503 구간」). 503을 받은 요청은 switchover 검색 3회(각 1건, 13.0~18.2초 동안 재시도)와 S1 업로드 1건(11.5초)뿐이고 모두 백오프 안에서 성공했다
- 전 회차에서 3노드 다이제스트가 일치했고 error 잡은 0이었다. 원시 기록과 회차별 상세는 #110 코멘트

---

## 17. Barman 백업 노드 (#166)

§16 클러스터에 OpenSQL 공식 구성(문서 「Barman」 설치·설정 절)대로 백업 노드를 붙인다. 결정은 ADR-053, 운영·복원
절차는 `OPERATIONS.md` 「백업과 복원」이다. node4는 PostgreSQL을 띄우지 않으므로 **OpenSQL 라이선스가 필요 없다**
(라이선스는 DB 서버를 띄우는 노드에 `identified_by_host`로 묶인다).

**시작 전 스냅샷** — 클러스터를 바꾸기 전에 되돌릴 기준을 남긴다: `patronictl show-config`, 각 노드 `patroni.yml`,
`pg_replication_slots`, `\du`.

**1. node4 만들기 (2GB · 2vCPU면 충분)**

```bash
U=/Applications/UTM.app/Contents/MacOS/utmctl
$U clone opensql-ha-base --name node4      # 설치기 실행 전 베이스. MAC은 UTM 편집 화면에서 Random으로 재생성
$U start node4; $U ip-address node4        # DHCP 주소로 들어가
ssh <DHCP 주소> 'bash -s 4' < notes/ha110/02_personalize.sh   # hostname node4 · 192.168.64.204
```

**2. 배포판에서 PG 유틸리티와 Barman 설치** — 배포판 중 `barman`·`barman_agent`·`postgresql`·`scripts`만 옮긴다(122MB).

```bash
sudo dnf install -y python3-argcomplete python3-pyyaml rsync
T=~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720
sudo bash -c "export OPENSQL_HOME=/opt/opensql PG_HOME=/opt/opensql PG_DATA_DIR=/opt/opensql/data-unused
  cd $T && . ./scripts/setenv.sh $T/scripts/setenv.sh && export OPENSQL_INSTALL_HOME=$T
  bash scripts/install.sh postgresql && bash scripts/install.sh barman"     # PostgreSQL 서비스는 띄우지 않는다
barman --version                          # 3.11.1 — /usr/local/bin. sudo의 secure_path에는 없어 절대 경로로 부른다
sudo useradd --system --create-home --home-dir /var/lib/barman --shell /bin/bash barman
```

**3. Barman 설정** — 모델 이름은 Patroni 멤버 이름(`postgresql1~3`)과 같아야 한다.

```ini
# /etc/barman.conf
[barman]
barman_user = barman
configuration_files_directory = /etc/barman.d
barman_home = /var/lib/barman
log_file = /var/log/barman/barman.log
log_level = INFO

# /etc/barman.d/opensql.conf — [postgresql2]·[postgresql3]도 host만 바꿔 같은 모양
[opensql]
cluster = opensql
conninfo = host=<지금 Leader> port=5432 user=barman dbname=postgres
streaming_conninfo = host=<지금 Leader> port=5432 user=streaming_barman dbname=postgres
backup_method = postgres
streaming_archiver = on
slot_name = barman
path_prefix = /opt/opensql/bin
retention_policy = RECOVERY WINDOW OF 7 DAYS
minimum_redundancy = 1

[postgresql1]
cluster = opensql
model = true
conninfo = host=192.168.64.201 port=5432 user=barman dbname=postgres
streaming_conninfo = host=192.168.64.201 port=5432 user=streaming_barman dbname=postgres
```

`~barman/.pgpass`(0600)에 두 롤의 비밀번호, crontab에 `* * * * * /usr/local/bin/barman cron`.

**4. 클러스터 변경 4건** (되돌리기는 각 줄의 역순)

```sql
-- C1. Primary에서 postgres로 (opensql 롤은 슈퍼유저가 아니다). credcheck가 비밀번호 정책을 건다
CREATE ROLE barman LOGIN PASSWORD '…';
GRANT pg_monitor, pg_checkpoint TO barman;
GRANT EXECUTE ON FUNCTION pg_backup_start(text, boolean), pg_backup_stop(boolean),
      pg_switch_wal(), pg_create_restore_point(text) TO barman;
CREATE ROLE streaming_barman LOGIN REPLICATION PASSWORD '…';
```

```bash
# C2. 각 노드 patroni.yml pg_hba (이 설치에선 DCS가 아니라 로컬 파일) — 일반 접속은 기존 `host all all all md5`로 된다
    - host replication streaming_barman 192.168.64.204/32 md5
PY=/home/opensql/etc/patroni/patroni.yml   # patronictl은 opensql 사용자로
# C3. 영구 슬롯 — Patroni가 세 노드 모두에 만든다
printf 'slots:\n  barman:\n    type: physical\n' | patronictl -c $PY edit-config --apply - --force
# C4. 각 노드 patroni.yml의 postgresql: 아래
  callbacks:
    on_role_change: "curl 'http://192.168.64.204:8080/renew_config'"
patronictl -c $PY reload opensql --force     # 로그에 오류가 없는지 본다 — YAML이 틀리면 옛 설정으로 계속 돈다
```

**5. Agent와 첫 백업**

```bash
A=$T/barman_agent
sudo install -m 755 $A/server.py /usr/local/bin/barman-agent
# /var/lib/barman/config.yml: listen "192.168.64.204", port 8080, cluster opensql, patroni [.201~.203:8008]
sudo cp $A/barman-agent.service /usr/lib/systemd/system/ && sudo systemctl enable --now barman-agent
for ip in 201 202 203; do sudo firewall-cmd --permanent \
  --add-rich-rule="rule family=ipv4 source address=192.168.64.$ip/32 port port=8080 protocol=tcp accept"; done
sudo firewall-cmd --reload                  # Agent HTTP에는 인증이 없다 — node1~3에만 연다

B="sudo -u barman -i /usr/local/bin/barman"
$B cron                                     # 서버 디렉터리가 이때 생긴다 — 그 전 config-switch는 실패한다
$B config-switch opensql <지금 Leader 멤버 이름>
$B backup opensql && $B switch-wal opensql
$B check opensql                            # 종료 코드 0
```

**검증** — switchover를 한 번 걸어 Agent 로그에 `Applying model '<새 Leader>'`, `ps`의 `pg_receivewal`이 새 Primary를
가리키는지 본다(실측 17~18초). 복원 시험은 `OPERATIONS.md` 「복원 절차」.

**네트워크 분리 회차 (#192)** — Leader 하나를 다른 DB 노드에서만 끊고 node4 경로는 살린 분리. §16 h165-part와 같은
형태이고, Barman을 붙인 뒤에 처음 쟀다.

| 회차 | 조건 | Barman | 복구 |
|---|---|---|---|
| 10/5 (#185 실측) | node1을 node2·3에서 90초 차단 | 옛 Primary(TL 46)에서 분기 WAL을 받아 `1/16000000`까지 감 → 새 Leader(TL 47)에 `1/16000000 (TL46)` 요청이 매분 거부, 17:21~18:40 백업 공백 | 슬롯이 지워진 WAL을 가리켜 2안(slot advance → `--reset` → 새 백업). 공백 구간 PITR 불가 |
| 10/7 재현 | 같은 차단 + 분리 중 node1에 직결 쓰기(분기 WAL을 확실히 만들기 위해) | 분리부터 새 Leader 콜백까지 29초 동안 옛 Primary에서 TL51 세그먼트 36·37·38을 받음. 분기점 TL51 `1/35B3D690`. 같은 오류로 정지, 분기 세그먼트가 `wals/`에 보관됨 | 1안(`.partial` 격리) — 슬롯 `restart_lsn 1/35000000`부터 받아 TL52로 스스로 전환, **공백 없음** |

원인: Barman은 노드 주소로 직접 스트리밍하고 주소 전환은 새 Leader의 `on_role_change` 콜백 한 번뿐이다. 옛 Primary는
Patroni가 강등시키기 전(10/7 ~22초)까지 쓰기를 받는다. `pg_receivewal`은 시작 위치를 슬롯이 아니라 로컬 `.partial`에서
정한다. 감지·복구 절차는 `OPERATIONS.md` 「네트워크 분리 뒤 수신 정지」. 자동 복구는 두지 않았다(ROADMAP 후보).

**6. 복원 재현 준비** — 복원 대상 노드(예: node2)에 rsync와 Barman 클라이언트를 깔아 둔다. SSH 키는 재현할 때만
양방향으로 넣고 끝나면 지운다. 재현 명령은 `scripts/dr_restore.py`(`OPERATIONS.md` 「복원 재현」)이다.

- `rsync` — 원격 `recover`가 대상에서 부른다. 없으면 복사 단계에서 실패한다
- Barman 클라이언트 — 2단계와 같은 방식으로 `install.sh barman`만(PostgreSQL은 이미 있다). `--get-wal` 복원의
  `restore_command`가 `/usr/local/bin/barman-wal-restore`를 부른다

```bash
# node4 barman → 대상 opensql, 대상 opensql → node4 barman 공개키를 각각 authorized_keys에 넣는다
sudo -u barman -i ssh opensql@192.168.64.202 hostname     # node4에서 — 비밀번호 없이 node2가 나와야 한다
```

## 부록: 붙여넣기 주의

에뮬레이션 콘솔과 SSH 모두에서 겪은 문제다.

- **heredoc**(`<<'PY'`)은 붙여넣기 시 각 줄에 들여쓰기가 붙어 종료 마커를 인식하지 못한다
- **긴 한 줄 명령**은 터미널 폭에서 잘려 `&&` 뒤가 별도 명령으로 실행된다
- **줄 끝 백슬래시**로 이어 쓰면 공백이 누락돼 인자가 붙어버린다 (`192.168.64.4/24\` + `ipv4.gateway` → `24ipv4.gateway`)

**여러 줄 명령은 한 줄씩 나눠 실행한다.**
