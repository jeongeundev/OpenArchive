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
#
# 판정은 **앱이 쓸 DSN으로 쓰기 가능한 연결이 서는가** 하나로 한다.
#
#   patronictl list로 판정하지 않는다 — 그 출력은 DCS(etcd)에 남은 마지막 기록이라
#   Patroni·PostgreSQL·OpenProxy가 전부 죽어 있어도 `Leader | running`을 보여준다.
#   2026-08-19 실측에서 인스턴스를 켠 직후가 정확히 그 상태였고, 이 검사가 거짓 통과했다.
#
#   포트 리스닝(ss -tln)만 보는 것도 부족하다. OpenProxy는 커넥션 풀러라 백엔드
#   PostgreSQL이 죽어도 자기는 리스닝하고, dbname 자리가 풀 이름이라 설정이 어긋나면
#   포트가 열린 채로 연결만 실패한다. 연결이 서더라도 Replica로 라우팅되면 마이그레이션이
#   read-only로 죽는다.
#
# 쿼리 한 번이 프로세스 생존·네트워크·자격증명과 풀 이름·쓰기 가능 여부를 함께 확인한다.
PSQL=/home/opensql/bin/psql
db_probe=$(sudo -u opensql env PGCONNECT_TIMEOUT=5 "$PSQL" "$DATABASE_URL" \
  -tAXc 'select pg_is_in_recovery()' 2>&1) || {
  fail "DB에 연결하지 못했다 — 배포를 시작할 수 없다.
  $db_probe
  OpenSQL을 먼저 기동한다:
  sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_patroni.sh'
  sudo -u opensql -i bash -c 'export OPENSQL_HOME=/home/opensql; bash /home/opensql/scripts/start_openproxy.sh'"
}
if [[ "$(echo "$db_probe" | tr -d '[:space:]')" != "f" ]]; then
  fail "DB에 연결은 되지만 쓰기할 수 없다 (pg_is_in_recovery=$db_probe).
  Replica로 라우팅됐거나 승격 전이다 — 마이그레이션이 read-only로 실패한다."
fi
echo "DB 접속: $DATABASE_URL (쓰기 가능 확인)"

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
# **재부팅 생존은 여전히 없다.** OpenSQL 자신도 nohup 맨 프로세스로 도는 설치라 앱만
# 부팅 시 살려도 붙을 DB가 없다. 인스턴스를 켤 때마다 이 스크립트를 돌린다.
#
# 다만 워커만은 systemd 유닛으로 돌린다 — 그건 재부팅이 아니라 **crash 복구**의 문제이고
# (DB는 살아 있는데 워커만 죽는 경우), 되살리지 않으면 임베딩 파이프라인이 통째로 멈춰
# 새 문서가 영원히 검색되지 않는다. API·프론트는 죽으면 사용자가 즉시 알아차리므로
# nohup 그대로 둔다 (ADR-038).
step "앱 3종 기동"

pkill -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -f 'next-server' 2>/dev/null || true

# 워커는 pkill 하지 않는다 — systemctl stop이 SIGTERM 경로로 세운다. pkill로 죽이면
# Restart=always가 즉시 되살려 낡은 코드의 워커가 배포 내내 되살아난다.
#
# **system 유닛이 아니라 user 유닛이다** — SELinux Enforcing에서 system 유닛(init_t)은
# 홈 아래(user_home_t)의 venv를 실행하지 못한다(203/EXEC, 2026-08-19 실측). 자세한
# 근거는 openarchive-worker.service 머리말과 ADR-038에 있다.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
# 세션이 끊겨도 사용자 매니저가 살아 있어야 워커가 유지된다. 이 한 줄만 sudo를 쓴다.
sudo loginctl enable-linger "$(id -un)"

mkdir -p "$HOME/.config/systemd/user"
sed -e "s|@APP_ROOT@|$APP_ROOT|g" \
  "$APP_ROOT/scripts/openarchive-worker.service" \
  > "$HOME/.config/systemd/user/openarchive-worker.service"
systemctl --user daemon-reload

# 세우는 것은 여기서, 띄우는 것은 **API가 준비된 뒤**다 (아래 "임베딩 워커 기동").
# 마이그레이션 실행 주체는 API 서버 하나이므로(ADR-012), 워커를 먼저 띄우면 스키마가
# 없는 동안 처리 루프가 예외로 돈다. 프로세스가 죽지는 않지만(run_worker가 예외를 삼키고
# 다음 폴링에서 재시도한다) journald에 트레이스백만 쌓인다.
# stop은 유닛이 inactive여도 성공한다 — 첫 배포에서도 안전하다.
systemctl --user stop openarchive-worker
# 직전 배포가 재시작 한도에 걸린 채 끝났다면 start가 거부된다 — 배포는 깨끗한 상태에서
# 시작해야 하므로 실패 카운터를 지운다 (실측에서 이 상태에 걸렸다).
systemctl --user reset-failed openarchive-worker 2>/dev/null || true

cd "$APP_ROOT/backend"
# API는 127.0.0.1에만 연다. 외부에 노출되는 것은 프론트뿐이고, /api/*는 Next.js가
# rewrite로 프록시한다 (next.config.ts). 신원은 세션 쿠키 검증으로만 해석되지만
# (ADR-028), API를 직접 열면 로그인·쓰기 엔드포인트가 그대로 인터넷에 노출된다.
setsid nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  </dev/null > "$HOME/api.log" 2>&1 &

cd "$APP_ROOT/frontend"
setsid env BACKEND_URL=http://127.0.0.1:8000 PORT=3000 HOSTNAME=0.0.0.0 \
  nohup npm run start </dev/null > "$HOME/web.log" 2>&1 &

# --- 확인 ------------------------------------------------------------------
step "API·프론트 기동 확인"

# `/api/system/status`는 세션 인증을 요구하므로(ADR-028) 익명 요청에는 401을 준다.
# `curl -f`로는 그 401이 실패로 잡혀 멀쩡한 배포가 통째로 실패한다 — 인증 도입 전에
# 쓰던 검사가 그대로 남아 있던 자리다.
#
# 여기서 확인할 것은 "응답이 오는가"이고, 그것이 곧 마이그레이션 완료의 증거다:
# uvicorn은 lifespan startup(= 마이그레이션, ADR-012)이 끝난 뒤에 리스닝을 시작한다.
# 3000으로 물어보므로 Next.js의 `/api/*` rewrite까지 한 번에 검증된다.
api_ready=""
for _ in $(seq 1 20); do
  sleep 3
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:3000/api/system/status || true)
  case "$code" in
    200 | 401)
      api_ready=1
      break
      ;;
  esac
done
[[ -n "$api_ready" ]] \
  || fail "API·프론트가 60초 안에 응답하지 않았다(마지막 상태코드: ${code:-없음}). ~/api.log ~/web.log 를 확인한다"

# --- 워커 -----------------------------------------------------------------
# 여기서 띄운다 — 위 응답이 곧 마이그레이션 완료의 증거다 (ADR-012).
step "임베딩 워커 기동"
systemctl --user start openarchive-worker

# 워커는 HTTP로 확인할 수 없어 프로세스 상태를 systemd에 직접 묻는다. 여기서 걸러야
# "화면은 뜨는데 임베딩만 안 되는" 배포를 배포 시점에 잡는다. `Type=simple`은 exec 직후
# active가 되지만, 기동 실패로 재시작 중이면 `activating`이라 is-active가 실패를 낸다 —
# RestartSec(10초)보다 넉넉히 두고 확인한다.
sleep 12
systemctl --user is-active --quiet openarchive-worker \
  || fail "임베딩 워커가 기동하지 않았다: journalctl --user -u openarchive-worker -n 50"

echo
echo "완료: http://<공인 IP>:3000 (보안그룹에서 3000을 열어야 접근된다)"
echo "  · 시스템 상태는 로그인 후 /admin/status에서 본다 — 익명 요청은 401이다 (ADR-028)"
echo "로그: ~/api.log ~/web.log · 워커는 journalctl --user -u openarchive-worker -f"
