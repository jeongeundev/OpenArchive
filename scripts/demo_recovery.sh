#!/usr/bin/env bash
set -euo pipefail

DB_STOP_CMD="${DB_STOP_CMD:-docker compose stop db}"
DB_START_CMD="${DB_START_CMD:-docker compose start db}"
DATABASE_URL="${DATABASE_URL:-postgresql://openarchive:openarchive@localhost:5433/openarchive}"
API_PORT="${API_PORT:-18000}"
API_URL="http://127.0.0.1:${API_PORT}"
PYTHON="backend/.venv/bin/python"
DEMO_USER="recovery-demo"

API_PID=""
WORKER_PID=""
DB_WAS_STOPPED=0
DOCUMENT_IDS=()
LAST_DOCUMENT_ID=""
TMP_DIR="$(mktemp -d)"
API_LOG="$TMP_DIR/api.log"
WORKER_LOG="$TMP_DIR/worker.log"

fail() {
  echo "실패: $*" >&2
  echo "API 로그: $API_LOG" >&2
  echo "워커 로그: $WORKER_LOG" >&2
  exit 1
}

run_db_command() {
  local command="$1"
  bash -c "$command"
}

db_value() {
  local metric="$1"
  DATABASE_URL="$DATABASE_URL" "$PYTHON" -c '
import os, sys
import psycopg

queries = {
    "ready": "SELECT 1",
    "documents": "SELECT count(*) FROM documents",
    "chunks": "SELECT count(*) FROM document_chunks",
}
query = queries[sys.argv[1]]
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print(conn.execute(query).fetchone()[0])
' "$metric"
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
  curl -sS --max-time 2 "$API_URL/api/system/status" >&2 || true
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

worker_logged_db_failure() {
  grep -q "처리 루프 실패" "$WORKER_LOG"
}

worker_logged_zombie_recovery() {
  grep -Eq "좀비 잡 [0-9]+건을 pending으로 회수" "$WORKER_LOG"
}

processing_visible() {
  local count
  count="$(status_value jobs.processing 2>/dev/null || echo 0)"
  (( count >= 1 ))
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if (( DB_WAS_STOPPED )); then
    run_db_command "$DB_START_CMD" >/dev/null 2>&1 || true
    wait_until 30 "정리 중 DB 재기동" db_ready >/dev/null 2>&1 || true
    DB_WAS_STOPPED=0
  fi

  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    for document_id in "${DOCUMENT_IDS[@]}"; do
      curl -fsS --max-time 5 -X DELETE \
        -H "X-User-Id: $DEMO_USER" \
        "$API_URL/api/documents/$document_id" >/dev/null 2>&1 || true
    done
  fi

  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill -CONT "$WORKER_PID" 2>/dev/null || true
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
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
  local version="$2"
  local body_file="$3"
  curl -fsS --max-time 10 -X PUT \
    -H "X-User-Id: $DEMO_USER" \
    -H "Content-Type: application/json" \
    --data-binary "@$body_file" \
    "$API_URL/api/documents/$document_id" >/dev/null
}

[[ -x "$PYTHON" ]] || fail "$PYTHON 실행 파일이 필요합니다"

echo "복구 데모 시작: 애플리케이션 재연결, 잡 재개, 좀비 회수, 정합성 수렴을 검증합니다."

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

printf '%s\n' "복구 데모 기준 문서 1" >"$TMP_DIR/a.txt"
printf '%s\n' "복구 데모 기준 문서 2" >"$TMP_DIR/b.txt"
upload_document "$TMP_DIR/a.txt"
DOC_A="$LAST_DOCUMENT_ID"
upload_document "$TMP_DIR/b.txt"
wait_until 30 "기준 문서 임베딩" jobs_drained || fail "기준 문서의 잡이 완료되지 않았습니다"
wait_until 10 "기준 정합성" consistent || fail "기준 상태의 정합성 카운터가 0이 아닙니다"

BASE_DOCUMENTS="$(db_value documents)"
BASE_CHUNKS="$(db_value chunks)"
echo "A-1 기준 상태: documents=$BASE_DOCUMENTS, chunks=$BASE_CHUNKS, inconsistent=0"

kill -STOP "$WORKER_PID"
"$PYTHON" -c 'import json,sys; json.dump({"content":"복구 데모 기준 문서 1 수정", "version":1}, open(sys.argv[1], "w"))' "$TMP_DIR/edit-a.json"
edit_document "$DOC_A" 1 "$TMP_DIR/edit-a.json"
[[ "$(status_value jobs.pending)" == "1" ]] || fail "A-3 pending 잡이 1건이 아닙니다"
[[ "$(status_value inconsistent_documents)" == "1" ]] || fail "A-3 정합성 어긋남이 1건이 아닙니다"
echo "A-3 DB 정지 전: pending=1, inconsistent=1"

DB_WAS_STOPPED=1
run_db_command "$DB_STOP_CMD" || fail "DB 정지 명령이 실패했습니다"
kill -CONT "$WORKER_PID"
wait_until 45 "워커의 DB 연결 실패 로그" worker_logged_db_failure || fail "워커에서 DB 연결 실패를 관측하지 못했습니다"
kill -0 "$WORKER_PID" 2>/dev/null || fail "DB 정지 중 워커 프로세스가 종료됐습니다"

SEARCH_CODE="$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' \
  -X POST -H "Content-Type: application/json" \
  --data '{"query":"복구"}' "$API_URL/api/search" || true)"
[[ "$SEARCH_CODE" != 2* ]] || fail "DB 정지 중 검색이 성공했습니다"
echo "A-5 중단 구간: worker_alive=yes, search_http=$SEARCH_CODE, failure_log=observed"

run_db_command "$DB_START_CMD" || fail "DB 재기동 명령이 실패했습니다"
DB_WAS_STOPPED=0
wait_until 30 "DB 접속 복구" db_ready || fail "DB 접속이 복구되지 않았습니다"
wait_until 45 "A 시나리오 잡 재개" jobs_drained || fail "A 시나리오 잡이 0으로 수렴하지 않았습니다"
wait_until 10 "A 시나리오 정합성 수렴" consistent || fail "A 시나리오 정합성이 0으로 수렴하지 않았습니다"
[[ "$(db_value documents)" == "$BASE_DOCUMENTS" ]] || fail "A 시나리오에서 문서 수가 달라졌습니다"
echo "A-7 복구 후: pending 1 -> 0, inconsistent 1 -> 0, documents=$BASE_DOCUMENTS"

LONG_TEXT_FILE="$TMP_DIR/long.txt"
"$PYTHON" -c 'import sys; open(sys.argv[1], "w").write(("좀비 회수 검증용 긴 추출 텍스트 문단입니다. " * 5000) + "\n")' "$LONG_TEXT_FILE"
LONG_DOCUMENT_IDS=()
for index in 1 2 3 4 5 6; do
  printf '복구 데모 추가 문서 %s\n' "$index" >"$TMP_DIR/short-$index.txt"
  upload_document "$TMP_DIR/short-$index.txt"
  LONG_DOCUMENT_IDS+=("$LAST_DOCUMENT_ID")
done
wait_until 45 "B 시나리오 기준 문서 임베딩" jobs_drained || fail "B 시나리오 기준 잡이 완료되지 않았습니다"
kill -STOP "$WORKER_PID"
"$PYTHON" -c 'import json,sys; json.dump({"content":open(sys.argv[1]).read(), "version":1}, open(sys.argv[2], "w"))' "$LONG_TEXT_FILE" "$TMP_DIR/edit-long.json"
for document_id in "${LONG_DOCUMENT_IDS[@]}"; do
  edit_document "$document_id" 1 "$TMP_DIR/edit-long.json"
done
[[ "$(status_value jobs.pending)" == "6" ]] || fail "B-1 pending 잡이 6건이 아닙니다"
echo "B-1 워커 정지 상태: pending=6"

kill -CONT "$WORKER_PID"
wait_until 15 "processing 잡 관측" processing_visible || fail "실제 워커가 선점한 processing 잡을 포착하지 못했습니다"
kill -KILL "$WORKER_PID"
wait "$WORKER_PID" 2>/dev/null || true
WORKER_PID=""
PROCESSING_AFTER_KILL="$(status_value jobs.processing)"
(( PROCESSING_AFTER_KILL >= 1 )) || fail "워커 종료 후 processing 잡이 남지 않았습니다"
echo "B-3 워커 강제 종료 후: processing=$PROCESSING_AFTER_KILL"

: >"$WORKER_LOG"
(
  cd backend
  exec env DATABASE_URL="$DATABASE_URL" EMBEDDING_PROVIDER=fake ZOMBIE_TIMEOUT_MINUTES=0 \
    .venv/bin/python -m app.worker
) >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!
wait_until 15 "좀비 잡 회수 로그" worker_logged_zombie_recovery || fail "좀비 잡 회수 로그를 관측하지 못했습니다"
wait_until 90 "B 시나리오 잡 완료" jobs_drained || fail "B 시나리오 잡이 0으로 수렴하지 않았습니다"
wait_until 10 "B 시나리오 정합성 수렴" consistent || fail "B 시나리오 정합성이 0으로 수렴하지 않았습니다"
echo "B-4 재기동 후: processing $PROCESSING_AFTER_KILL -> 0, pending 6 -> 0, inconsistent=0"

echo "완료: Single 구성에서 애플리케이션 복구 경로와 버전 일관성·최신 수렴을 확인했습니다."
echo "이 데모는 Patroni 리더 선출·승격과 그 소요 시간을 증명하지 않습니다."
