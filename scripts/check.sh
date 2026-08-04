#!/usr/bin/env bash
# 통합 검증 — backend + frontend.
# Stop 훅에서 매 응답마다 실행되므로, 아직 생성되지 않은 쪽은 건너뛴다.
#
# 첫 실패에서 중단하지 않고 모든 검증을 끝까지 돌린 뒤 종료 코드를 합산한다.
# 한 번의 실행으로 깨진 곳을 전부 보기 위함이다.

set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
FAILED=0

if [ -f "$ROOT/backend/pyproject.toml" ]; then
  echo "== backend: pytest =="
  cd "$ROOT/backend" && pytest || FAILED=1
else
  echo "== backend: 건너뜀 (backend/pyproject.toml 없음) =="
fi

if [ -f "$ROOT/frontend/package.json" ]; then
  cd "$ROOT/frontend" || exit 1
  echo "== frontend: lint =="
  npm run lint || FAILED=1
  echo "== frontend: test =="
  npm run test || FAILED=1
  echo "== frontend: build =="
  npm run build || FAILED=1
else
  echo "== frontend: 건너뜀 (frontend/package.json 없음) =="
fi

if [ "$FAILED" -ne 0 ]; then
  echo "검증 실패 — 위 출력에서 실패한 단계를 확인하세요."
fi

exit "$FAILED"
