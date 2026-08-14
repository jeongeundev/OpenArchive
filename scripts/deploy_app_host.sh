#!/usr/bin/env bash
# OpenSQL이 설치된 호스트에서 OpenArchive 앱 3종(API·임베딩 워커·프론트)을 기동한다.
# install_opensql_host.sh 가 DB를 세운 뒤, 같은 호스트에서 실행한다.
#
#   bash scripts/deploy_app_host.sh
#
# 소스는 맥에서 rsync로 미리 올려 둔다 (SETUP_OPENSQL.md §15).
# 2026-08-09 EC2 t3.large에서 이 절차 그대로 관통을 확인했다.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_IP=$(hostname -I | awk '{print $1}')
DATABASE_URL="${DATABASE_URL:-postgresql://postgres:pg_password@${NODE_IP}:6432/opensql}"

fail() { echo "실패: $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

# --- 사전 조건 -------------------------------------------------------------
step "사전 조건 확인"

command -v python3.12 >/dev/null || fail "python3.12가 없다: sudo dnf install -y python3.12 python3.12-devel"
command -v npm >/dev/null || fail "node가 없다: sudo dnf module install -y nodejs:22/common"

# OpenSQL은 재부팅해도 etcd만 살아난다 — Patroni·PostgreSQL·OpenProxy는 systemd 유닛이
# 없어 수동 기동이 필요하다. 앱만 띄우고 DB가 없는 상태를 여기서 끊는다.
PATRONICTL=/home/opensql/bin/patronictl
if ! sudo -u opensql "$PATRONICTL" -c /home/opensql/etc/patroni/patroni.yml list 2>/dev/null | grep -q running; then
  fail "PostgreSQL이 기동하지 않았다. 먼저 아래를 실행한다:
  sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_patroni.sh'
  sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_openproxy.sh'"
fi
echo "DB 접속: $DATABASE_URL"

# --- 백엔드 ----------------------------------------------------------------
step "백엔드 의존성 설치"
cd "$APP_ROOT/backend"

# CPU 전용 torch를 먼저 넣는다. 기본 PyPI wheel은 CUDA 빌드라 GPU가 없는 인스턴스에
# nvidia-* 2.7GB를 끌고 온다 (venv 5.1GB → 1.4GB, 설치 2m37s → 1m16s).
python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -q -e ".[local]"

printf "%s\n" \
  "DATABASE_URL=$DATABASE_URL" \
  "EMBEDDING_PROVIDER=local" > .env

# --- 프론트엔드 ------------------------------------------------------------
step "프론트엔드 빌드"
cd "$APP_ROOT/frontend"
npm ci --no-audit --no-fund
BACKEND_URL=http://127.0.0.1:8000 npm run build

# --- 기동 ------------------------------------------------------------------
# systemd 유닛을 만들지 않는다. OpenSQL 자신도 nohup 맨 프로세스로 도는 설치라
# 앱만 재부팅 생존시켜도 DB가 없어 의미가 없다. 인스턴스를 켤 때마다 이 스크립트를 돌린다.
step "앱 3종 기동"

pkill -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -f 'python -m app.worker' 2>/dev/null || true
pkill -f 'next-server' 2>/dev/null || true

cd "$APP_ROOT/backend"
# API는 127.0.0.1에만 연다. 외부에 노출되는 것은 프론트뿐이고, /api/*는 Next.js가
# rewrite로 프록시한다 (next.config.ts). 신원은 세션 쿠키 검증으로만 해석되지만
# (ADR-028), API를 직접 열면 로그인·쓰기 엔드포인트가 그대로 인터넷에 노출된다.
setsid nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  </dev/null > "$HOME/api.log" 2>&1 &
setsid nohup .venv/bin/python -m app.worker \
  </dev/null > "$HOME/worker.log" 2>&1 &

cd "$APP_ROOT/frontend"
setsid env BACKEND_URL=http://127.0.0.1:8000 PORT=3000 HOSTNAME=0.0.0.0 \
  nohup npm run start </dev/null > "$HOME/web.log" 2>&1 &

# --- 확인 ------------------------------------------------------------------
step "기동 확인"
for _ in $(seq 1 20); do
  sleep 3
  if curl -sf -m 5 http://127.0.0.1:3000/api/system/status >/dev/null 2>&1; then
    curl -s http://127.0.0.1:3000/api/system/status; echo
    echo
    echo "완료: http://<공인 IP>:3000 (보안그룹에서 3000을 열어야 접근된다)"
    echo "로그: ~/api.log ~/worker.log ~/web.log"
    exit 0
  fi
done

fail "3종이 60초 안에 응답하지 않았다. ~/api.log ~/worker.log ~/web.log 를 확인한다"
