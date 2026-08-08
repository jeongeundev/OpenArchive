#!/usr/bin/env bash
# Rocky Linux 9.7 x86-64 호스트에 OpenSQL을 single 모드로 설치한다.
# UTM VM과 AWS EC2 양쪽에서 같은 절차를 밟기 위한 스크립트다.
#
#   bash scripts/install_opensql_host.sh ~/Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720
#
# 설치 파일은 상용 배포판이라 저장소에 넣지 않는다. 경로만 인자로 받는다.
set -euo pipefail

INSTALLER_ROOT="${1:-}"
[[ -n "$INSTALLER_ROOT" ]] || { echo "사용법: $0 <설치 파일 디렉터리>" >&2; exit 1; }
INSTALLER="$INSTALLER_ROOT/opensql-installer"
[[ -d "$INSTALLER" ]] || { echo "실패: $INSTALLER 가 없습니다" >&2; exit 1; }

LICENSE_DIR="$INSTALLER/licenses"
CONFIG="$INSTALLER/config/common.env"

fail() { echo "실패: $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

# --- 사전 조건 -------------------------------------------------------------
# 셋 다 실제로 설치를 깨뜨린 적이 있는 항목이다. 설치기가 16%에서 죽고 나서
# 원인을 찾는 것보다, 여기서 먼저 끊는 편이 싸다.
step "사전 조건 확인"

[[ "$(uname -m)" == "x86_64" ]] || fail "x86-64 전용이다 (현재: $(uname -m))"

RELEASE=$(sed -n 's/^Rocky Linux release \([0-9.]*\).*/\1/p' /etc/rocky-release 2>/dev/null || true)
[[ "$RELEASE" == "9.7" ]] || fail "Rocky Linux 9.7이 필요하다 (현재: ${RELEASE:-알 수 없음}). 9.8에서는 glibc가 충돌한다"

LICENSE_FILE=$(find "$LICENSE_DIR" -maxdepth 1 -name '*.xml' -print -quit 2>/dev/null || true)
[[ -n "$LICENSE_FILE" ]] || fail "$LICENSE_DIR 에 라이선스 xml이 없다"

# 라이선스가 hostname과 CPU 수에 묶여 있다. 어긋나면 PostgreSQL이 기동하지 않는다.
LICENSE_HOST=$(sed -n 's|.*<identified_by_host>\(.*\)</identified_by_host>.*|\1|p' "$LICENSE_FILE")
LICENSE_CPU=$(sed -n 's|.*<limit_cpu>\(.*\)</limit_cpu>.*|\1|p' "$LICENSE_FILE")
LICENSE_END=$(sed -n 's|.*<end_date>\(.*\)</end_date>.*|\1|p' "$LICENSE_FILE")

[[ "$(hostname)" == "$LICENSE_HOST" ]] ||
  fail "hostname이 라이선스와 다르다: 현재 '$(hostname)', 요구 '$LICENSE_HOST'"
(( $(nproc) <= LICENSE_CPU )) ||
  fail "CPU가 라이선스 상한을 넘는다: 현재 $(nproc)개, 상한 ${LICENSE_CPU}개"

echo "hostname=$(hostname)  CPU=$(nproc)/$LICENSE_CPU  라이선스 만료=$LICENSE_END"

# --- 패키지 ----------------------------------------------------------------
step "필수 패키지 설치"
sudo dnf install -y \
  tar gcc make gettext jq pkgconf-pkg-config \
  openssl-devel clang-devel python3-psycopg2 \
  bison flex krb5-devel lz4-devel protobuf-c \
  readline-devel zlib-devel \
  libxml2 lz4-libs ncurses-libs readline zlib \
  libxslt perl-libs

# PostGIS 런타임 의존성. 이 프로젝트는 PostGIS를 쓰지 않지만 설치기가 검사한다.
sudo dnf install -y epel-release
sudo dnf install -y geos proj gdal SFCGAL

# --- 설치기 설정 -----------------------------------------------------------
# 요구는 SFCGAL 2.0.0 이상인데 EPEL 9에는 1.5.0뿐이다. PGDG를 추가하면 PostgreSQL
# 패키지가 겹쳐 OpenSQL 자체 PG 17.8과 충돌하므로, 요구 버전만 완화한다.
step "SFCGAL 버전 요구 완화"
cd "$INSTALLER"
cp -n src/package_requirements.json src/package_requirements.json.bak
jq '(..|objects|select(.name_pattern=="SFCGAL" or .name=="SFCGAL")|.min_version)="1.5.0"' \
  src/package_requirements.json >/tmp/package_requirements.json
mv /tmp/package_requirements.json src/package_requirements.json

step "common.env 설정"
# Patroni·etcd가 바인딩할 주소다. 클라우드에서는 공인 IP가 아니라 사설 IP여야 한다.
NODE_IP=$(hostname -I | awk '{print $1}')
sed -i "s|^NODE1_IP=.*|NODE1_IP=\"$NODE_IP\"|"                    config/common.env
sed -i "s|^NODE_NAME=.*|NODE_NAME=\"$(hostname)\"|"               config/common.env
sed -i "s|^OPENSQL_HOME=.*|OPENSQL_HOME=\"/home/opensql\"|"       config/common.env
sed -i "s|^PG_HOME=.*|PG_HOME=\"/home/opensql\"|"                 config/common.env
sed -i "s|^PG_DATA_DIR=.*|PG_DATA_DIR=\"/home/opensql/data/pgsql\"|" config/common.env
sed -i "s|^LICENSE_NAME=.*|LICENSE_NAME=\"$(basename "$LICENSE_FILE")\"|" config/common.env
grep -E "^(NODE1_IP|NODE_NAME|OPENSQL_HOME|PG_HOME|PG_DATA_DIR|LICENSE_NAME)=" "$CONFIG"

# --- 설치 ------------------------------------------------------------------
step "OpenSQL 설치 (single 모드)"
sudo python3 opensql_local_installer.py --mode single

# --- 검증 ------------------------------------------------------------------
# 설치기가 성공을 찍어도 4개 컴포넌트가 다 살아 있는지는 별개다.
step "설치 확인"
sudo -u opensql /home/opensql/bin/patronictl \
  -c /home/opensql/etc/patroni/patroni.yml list

sudo -u opensql env PGPASSWORD=pg_password /home/opensql/bin/psql \
  -h "$NODE_IP" -p 6432 -U postgres -d opensql \
  -c 'select version()' -c 'show shared_preload_libraries'

echo
echo "완료: OpenProxy는 ${NODE_IP}:6432 이다."
echo "라이선스는 ${LICENSE_END}에 만료되며, 그 후에는 PostgreSQL이 기동하지 않는다."
