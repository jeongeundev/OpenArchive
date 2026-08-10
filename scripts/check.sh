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
  cd "$ROOT/backend" || exit 1
  # 셸의 기본 python(pyenv system)에는 pytest·ruff가 없다.
  # 활성화 상태에 의존하지 않도록 .venv 안의 실행 파일을 직접 호출한다.
  VENV="$ROOT/backend/.venv"
  if [ ! -x "$VENV/bin/python" ]; then
    echo "== backend: 검증 불가 — backend/.venv 없음 =="
    echo "   생성: cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    FAILED=1
  else
    echo "== backend: ruff =="
    "$VENV/bin/ruff" check . || FAILED=1
    echo "== backend: pytest =="
    "$VENV/bin/pytest" || FAILED=1
  fi
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

echo "== scripts: 셸 구문 검사 =="
for f in "$ROOT"/scripts/*.sh "$ROOT"/scripts/hooks/*.sh; do
  [ -f "$f" ] || continue
  bash -n "$f" || FAILED=1
done

if [ "$FAILED" -ne 0 ]; then
  echo "검증 실패 — 위 출력에서 실패한 단계를 확인하세요."
fi

exit "$FAILED"
