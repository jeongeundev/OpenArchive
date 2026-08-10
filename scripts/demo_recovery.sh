#!/usr/bin/env bash
set -euo pipefail

OPENSQL_HOST="${OPENSQL_HOST:-192.168.64.4}"
OPENSQL_SSH="${OPENSQL_SSH:-$OPENSQL_HOST}"
DATABASE_URL="${DATABASE_URL:-postgresql://postgres:pg_password@$OPENSQL_HOST:6432/opensql}"
PATRONI_URL="${PATRONI_URL:-http://$OPENSQL_HOST:8008}"
PATRONI_LOG="${PATRONI_LOG:-/home/opensql/logs/patroni.log}"
API_PORT="${API_PORT:-18000}"
API_URL="http://127.0.0.1:${API_PORT}"
PYTHON="backend/.venv/bin/python"
DEMO_USER="recovery-demo"

API_PID=""
WORKER_PID=""
APP_PROBE_PID=""
DOCUMENT_IDS=()
LAST_DOCUMENT_ID=""
LAST_STAGE="스크립트 시작"
LAST_STAGE_AT="$(date '+%Y-%m-%d %H:%M:%S')"
TMP_DIR="$(mktemp -d)"
API_LOG="$TMP_DIR/api.log"
WORKER_LOG="$TMP_DIR/worker.log"

mark_stage() {
  LAST_STAGE="$1"
  LAST_STAGE_AT="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "단계: $LAST_STAGE ($LAST_STAGE_AT)"
}

fail() {
  echo "실패: $*" >&2
  echo "마지막 성공 단계: $LAST_STAGE ($LAST_STAGE_AT)" >&2
  echo "API 로그: $API_LOG" >&2
  echo "워커 로그: $WORKER_LOG" >&2
  exit 1
}

elapsed() {
  "$PYTHON" -c 'import sys, time; print(f"{time.time() - float(sys.argv[1]):.2f}s")' "$1"
}

db_value() {
  local metric="$1"
  DATABASE_URL="$DATABASE_URL" "$PYTHON" -c '
import os, sys
import psycopg

queries = {
    "ready": "SELECT 1",
    "schema": "SELECT to_regclass(%s) IS NOT NULL",
    "documents": "SELECT count(*) FROM documents",
    "active_jobs": "SELECT count(*) FROM embedding_jobs WHERE status = ANY(%s)",
    "inconsistent": "SELECT count(DISTINCT c.document_id) FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE c.version <> d.version",
}
query = queries[sys.argv[1]]
params = {
    "schema": ("documents",),
    "active_jobs": (["pending", "processing"],),
}.get(sys.argv[1])
with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
    print(conn.execute(query, params).fetchone()[0])
' "$metric"
}

patroni_value() {
  local path="$1"
  curl -fsS --max-time 3 "$PATRONI_URL" |
    "$PYTHON" -c '
import json, sys

value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value = value[part]
print(value)
' "$path"
}

status_value() {
  local path="$1"
  curl -fsS --max-time 3 "$API_URL/api/system/status" |
    "$PYTHON" -c '
import json, sys

value = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value = value[part]
print(value)
' "$path"
}

wait_until() {
  local timeout="$1"
  local description="$2"
  shift 2
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if "$@"; then
      return 0
    fi
    sleep 0.1
  done
  echo "타임아웃: $description" >&2
  return 1
}

api_ready() {
  curl -fsS --max-time 2 "$API_URL/api/system/status" >/dev/null 2>&1
}

db_ready() {
  db_value ready >/dev/null 2>&1
}

jobs_drained() {
  [[ "$(status_value jobs.pending 2>/dev/null)" == "0" ]] &&
    [[ "$(status_value jobs.processing 2>/dev/null)" == "0" ]]
}

consistent() {
  [[ "$(status_value inconsistent_documents 2>/dev/null)" == "0" ]]
}

app_exception_seen() {
  [[ -s "$TMP_DIR/app-exception.txt" ]]
}

patroni_log_contains() {
  local pattern="$1"
  ssh -o BatchMode=yes -o ConnectTimeout=4 "$OPENSQL_SSH" \
    "sudo -n tail -n +$((PATRONI_LOG_LINE + 1)) '$PATRONI_LOG' 2>/dev/null" |
    grep -Fq "$pattern"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill -CONT "$WORKER_PID" 2>/dev/null || true
  fi
  if [[ -n "$APP_PROBE_PID" ]] && kill -0 "$APP_PROBE_PID" 2>/dev/null; then
    kill "$APP_PROBE_PID" 2>/dev/null || true
    wait "$APP_PROBE_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    for document_id in ${DOCUMENT_IDS[@]+"${DOCUMENT_IDS[@]}"}; do
      curl -fsS --max-time 5 -X DELETE \
        -H "X-User-Id: $DEMO_USER" \
        "$API_URL/api/documents/$document_id" >/dev/null 2>&1 || true
    done
  fi
  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if (( exit_code == 0 )); then
    rm -rf "$TMP_DIR"
  else
    echo "진단 파일을 보존했습니다: $TMP_DIR" >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

upload_document() {
  local file="$1"
  local response document_id
  response="$(curl -fsS --max-time 10 -X POST \
    -H "X-User-Id: $DEMO_USER" \
    -F "file=@$file;type=text/plain" \
    "$API_URL/api/documents")"
  document_id="$(printf '%s' "$response" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  DOCUMENT_IDS+=("$document_id")
  LAST_DOCUMENT_ID="$document_id"
}

edit_document() {
  local document_id="$1"
  local body_file="$2"
  curl -fsS --max-time 10 -X PUT \
    -H "X-User-Id: $DEMO_USER" \
    -H "Content-Type: application/json" \
    --data-binary "@$body_file" \
    "$API_URL/api/documents/$document_id" >/dev/null
}

echo "복구 데모 시작: 실 OpenSQL의 DB 프로세스 장애 자동 복구를 관측합니다."

[[ -x "$PYTHON" ]] || fail "$PYTHON 실행 파일이 필요합니다"
"$PYTHON" -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3).close()' \
  "$OPENSQL_HOST" 6432 >/dev/null 2>&1 || fail "OpenSQL OpenProxy($OPENSQL_HOST:6432)에 접속할 수 없습니다"
curl -fsS --max-time 3 "$PATRONI_URL" >/dev/null || fail "Patroni REST($PATRONI_URL)에 접속할 수 없습니다"
ssh -o BatchMode=yes -o ConnectTimeout=4 "$OPENSQL_SSH" 'sudo -n true' >/dev/null || fail "SSH 또는 비밀번호 없는 sudo를 사용할 수 없습니다: $OPENSQL_SSH"
[[ "$(db_value schema 2>/dev/null)" == "True" ]] || fail "OpenProxy pool 대상 DB에 documents 테이블이 없습니다"
mark_stage "0. 사전 점검 완료"

BASE_ROLE="$(patroni_value role)"
BASE_TL="$(patroni_value timeline)"
BASE_DOCUMENTS="$(db_value documents)"
[[ "$(db_value ready)" == "1" ]] || fail "기준선 DB 쿼리가 실패했습니다"
[[ "$BASE_ROLE" == "primary" ]] || fail "기준선 Patroni role이 primary가 아닙니다: $BASE_ROLE"
[[ "$(db_value active_jobs)" == "0" ]] || fail "기준선에 미처리 잡이 남아 있습니다"
[[ "$(db_value inconsistent)" == "0" ]] || fail "기준선 정합성 카운터가 0이 아닙니다"
mark_stage "1. 기준선 확인: role=$BASE_ROLE, TL=$BASE_TL, inconsistent=0"

(
  cd backend
  exec env DATABASE_URL="$DATABASE_URL" EMBEDDING_PROVIDER=fake \
    .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT"
) >"$API_LOG" 2>&1 &
API_PID=$!
wait_until 30 "API 기동" api_ready || fail "API가 기동하지 않았습니다"

(
  cd backend
  exec env DATABASE_URL="$DATABASE_URL" EMBEDDING_PROVIDER=fake \
    .venv/bin/python -m app.worker
) >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

printf '%s\n' "OpenSQL DB 프로세스 장애 복구 데모 문서" >"$TMP_DIR/document.txt"
upload_document "$TMP_DIR/document.txt"
DOC_ID="$LAST_DOCUMENT_ID"
wait_until 30 "기준 문서 임베딩" jobs_drained || fail "기준 문서의 잡이 완료되지 않았습니다"
wait_until 10 "기준 정합성" consistent || fail "기준 문서의 정합성 카운터가 0이 아닙니다"

kill -STOP "$WORKER_PID"
"$PYTHON" -c 'import json,sys; json.dump({"content":"OpenSQL DB 프로세스 장애 중 재개할 수정 텍스트", "version":1}, open(sys.argv[1], "w"))' "$TMP_DIR/edit.json"
edit_document "$DOC_ID" "$TMP_DIR/edit.json"
[[ "$(status_value jobs.pending)" == "1" ]] || fail "장애 직전 pending 잡이 1건이 아닙니다"
[[ "$(status_value inconsistent_documents)" == "1" ]] || fail "장애 직전 정합성 어긋남이 1건이 아닙니다"
mark_stage "2. 파이프라인 가동: pending=1, inconsistent=1"

DATABASE_URL="$DATABASE_URL" PROBE_DIR="$TMP_DIR" "$PYTHON" -c '
import os, pathlib, time
import psycopg

root = pathlib.Path(os.environ["PROBE_DIR"])
with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
    root.joinpath("app-probe-ready").touch()
    while not root.joinpath("app-probe-trigger").exists():
        time.sleep(0.01)
    try:
        conn.execute("SELECT 1").fetchone()
    except psycopg.OperationalError as exc:
        name = f"{type(exc).__module__}.{type(exc).__name__}"
        root.joinpath("app-exception.txt").write_text(f"{time.time()}|{name}")
' >"$TMP_DIR/app-probe.log" 2>&1 &
APP_PROBE_PID=$!
wait_until 5 "앱 DB 프로브 연결" test -f "$TMP_DIR/app-probe-ready" || fail "장애 전 앱 DB 연결을 준비하지 못했습니다"

PATRONI_LOG_LINE=0
if ssh -o BatchMode=yes -o ConnectTimeout=4 "$OPENSQL_SSH" "sudo -n test -r '$PATRONI_LOG'"; then
  PATRONI_LOG_LINE="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$OPENSQL_SSH" "sudo -n wc -l < '$PATRONI_LOG'" 2>/dev/null || echo 0)"
else
  echo "참고: Patroni 로그를 찾거나 읽을 수 없습니다: $PATRONI_LOG (로그 사건은 미관측으로 계속합니다)"
fi

T0_EPOCH="$($PYTHON -c 'import time; print(time.time())')"
POSTMASTER_PID="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$OPENSQL_SSH" \
  "pid=\$(sudo -n head -1 /home/opensql/data/pgsql/postmaster.pid); test \"\$(sudo -n ps -o comm= -p \"\$pid\" | tr -d ' ')\" = postgres; sudo -n kill -9 \"\$pid\"; printf '%s' \"\$pid\"")" || fail "postmaster 부모 프로세스 SIGKILL에 실패했습니다"
touch "$TMP_DIR/app-probe-trigger"
kill -CONT "$WORKER_PID"
echo "★ 사람 개입 1회: t0 +0.00s postmaster PID $POSTMASTER_PID SIGKILL"
mark_stage "3. postmaster SIGKILL 완료"

wait_until 15 "애플리케이션 OperationalError 계열 예외" app_exception_seen || fail "앱 DB 클라이언트에서 연결 예외를 관측하지 못했습니다"
T1_EPOCH="$(cut -d '|' -f 1 "$TMP_DIR/app-exception.txt")"
T1_CLASS="$(cut -d '|' -f 2 "$TMP_DIR/app-exception.txt")"
T1="$($PYTHON -c 'import sys; print(f"{float(sys.argv[1]) - float(sys.argv[2]):.2f}s")' "$T1_EPOCH" "$T0_EPOCH")"

T2="미관측"
T3="미관측"
if (( PATRONI_LOG_LINE > 0 )); then
  if wait_until 15 "Patroni 장애 감지 로그" patroni_log_contains "Postgresql is not running"; then
    T2="$(elapsed "$T0_EPOCH")"
  fi
  if wait_until 15 "Patroni primary 재기동 로그" patroni_log_contains "starting primary after failure"; then
    T3="$(elapsed "$T0_EPOCH")"
  fi
fi

wait_until 30 "OpenProxy 6432 재접속" db_ready || fail "OpenProxy 6432를 통한 DB 접속이 복구되지 않았습니다"
T4="$(elapsed "$T0_EPOCH")"
wait_until 45 "pending 잡 재개" jobs_drained || fail "pending 잡이 0으로 수렴하지 않았습니다"
T5="$(elapsed "$T0_EPOCH")"
wait_until 10 "정합성 수렴" consistent || fail "inconsistent_documents가 0으로 수렴하지 않았습니다"
T6="$(elapsed "$T0_EPOCH")"
mark_stage "4. 자동 복구 관측 완료"

FINAL_TL="$(patroni_value timeline)"
FINAL_ROLE="$(patroni_value role)"
[[ "$FINAL_ROLE" == "primary" ]] || fail "복구 후 Patroni role이 primary가 아닙니다: $FINAL_ROLE"
[[ "$FINAL_TL" == "$BASE_TL" ]] || fail "Single 복구 중 timeline이 바뀌었습니다: TL $BASE_TL -> $FINAL_TL"
curl -fsS --max-time 5 -X DELETE -H "X-User-Id: $DEMO_USER" \
  "$API_URL/api/documents/$DOC_ID" >/dev/null || fail "데모 문서를 정리하지 못했습니다"
DOCUMENT_IDS=()
[[ "$(db_value documents)" == "$BASE_DOCUMENTS" ]] || fail "데모 문서 정리 후 기존 문서 수가 달라졌습니다"
mark_stage "5. 승격 없음 확인: role=$FINAL_ROLE, TL=$FINAL_TL"

echo
echo "복구 타임라인"
echo "  t0  +0.00s  postmaster PID $POSTMASTER_PID SIGKILL"
echo "  t1  +$T1  앱 첫 예외: $T1_CLASS"
echo "  t2  +$T2  Patroni: Postgresql is not running"
echo "  t3  +$T3  Patroni: starting primary after failure"
echo "  t4  +$T4  OpenProxy 6432 재접속 성공"
echo "  t5  +$T5  pending/processing 잡 0"
echo "  t6  +$T6  inconsistent_documents=0"
echo
echo "완료: DB 프로세스 장애로부터의 자동 복구를 확인했습니다."
echo "      Patroni가 감지하고 스스로 재기동했으며, 사람의 개입은 kill 1회뿐입니다."
echo
echo "한계: 노드 사망은 복구되지 않습니다. 노드 2대 이상이 물리적 전제이며,"
echo "      사무국 지시에 따른 Single 구성의 제약입니다."
echo "      리더 선출·승격은 일어나지 않았습니다 (timeline 유지: TL $FINAL_TL)."
