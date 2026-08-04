#!/bin/bash
# TDD Guard Hook — PreToolUse[Edit|Write]
# 구현 코드를 작성하려 할 때, 해당 모듈의 테스트 파일이 먼저 존재하는지 체크.
# 테스트 없이 구현 코드를 작성하려 하면 차단.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# 파일 경로가 없으면 통과
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# 테스트 파일 자체를 수정하는 건 허용.
# 경로 전체가 아니라 파일명으로 판정한다 — 상위 경로에 'test' 문자열이 들어 있다는
# 이유만으로 가드가 통째로 우회되거나, latest.ts/manifest.py 같은 이름이
# 테스트 파일로 오인되는 것을 막기 위함이다.
FILE_NAME=$(basename "$FILE_PATH")
case "$FILE_NAME" in
  test_*|*_test.*|*.test.*|*.spec.*)
    exit 0
    ;;
esac

# 테스트 디렉토리 안의 파일도 허용 (conftest.py, 픽스처 등)
case "$FILE_PATH" in
  */tests/*|*/test/*|*/__tests__/*)
    exit 0
    ;;
esac

# 설정/타입/스타일 파일은 테스트 불필요 — 허용
case "$FILE_PATH" in
  *.json|*.css|*.scss|*.md|*.yml|*.yaml|*.env*|*.config.*|*tailwind*|*postcss*|*next.config*|*tsconfig*)
    exit 0
    ;;
esac

# types/ 폴더는 테스트 불필요 — 허용
case "$FILE_PATH" in
  */types/*|*/types.ts|*/types.d.ts)
    exit 0
    ;;
esac

# Next.js 프레임워크 파일은 허용 (layout, page, loading, error, not-found, global styles)
case "$FILE_PATH" in
  */layout.tsx|*/layout.ts|*/page.tsx|*/page.ts|*/loading.tsx|*/error.tsx|*/not-found.tsx|*/globals.css)
    exit 0
    ;;
esac

# 마이그레이션 SQL 중 트리거·테이블 정의는 TDD 대상이다.
# 트리거(잡 생성·코얼레싱)와 테이블 제약이 이 프로젝트의 심사 핵심이라 테스트로 보호한다.
# 확장 설치(001_extensions)·인덱스(004_indexes)는 검증 가치가 낮아 제외한다.
case "$FILE_NAME" in
  *_triggers.sql | *_tables.sql)
    # 번호 접두사를 뗀 마지막 세그먼트로 매핑한다. 예: 003_triggers.sql → tests/test_triggers.py
    SQL_TOPIC="${FILE_NAME%.sql}"
    SQL_TOPIC="${SQL_TOPIC##*_}"
    PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || echo ".")}"

    if [ -z "$(find "${PROJECT_ROOT}/backend/tests" -maxdepth 1 -name "test_*${SQL_TOPIC}*.py" -print -quit 2>/dev/null)" ]; then
      cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: '${FILE_NAME}'의 동작을 검증하는 테스트가 없습니다. 트리거·제약은 원본-벡터 정합성의 핵심이므로 먼저 테스트를 작성하세요. (테스트 파일 예: backend/tests/test_${SQL_TOPIC}.py)"
  }
}
EOF
    fi
    exit 0
    ;;
esac

# Python 조립/선언 전용 파일은 테스트 불필요 — 허용
# (main.py=앱 조립, config.py=설정 선언, db.py=풀 생성, base.py=Protocol, fake.py=테스트용 구현)
case "$FILE_PATH" in
  */__init__.py|*/conftest.py|*/main.py|*/config.py|*/db.py|*/base.py|*/fake.py)
    exit 0
    ;;
esac

# Python 소스 파일이면 대응 테스트가 있는지 확인
case "$FILE_PATH" in
  *.py)
    DIR=$(dirname "$FILE_PATH")
    BASENAME=$(basename "$FILE_PATH" .py)
    PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || echo ".")}"

    TEST_FOUND=false

    # 같은 폴더의 test_<모듈명>.py (예: scripts/execute.py ↔ scripts/test_execute.py)
    if [ -f "${DIR}/test_${BASENAME}.py" ]; then
      TEST_FOUND=true
    fi

    # backend/tests/ — 테스트 파일명이 모듈명과 1:1이 아닌 경우가 있어 부분 일치로 찾는다.
    # 예: app/api/search.py → tests/test_search.py 또는 tests/test_search_api.py
    if [ "$TEST_FOUND" = false ] &&
      [ -n "$(find "${PROJECT_ROOT}/backend/tests" -maxdepth 1 -name "test_*${BASENAME}*.py" -print -quit 2>/dev/null)" ]; then
      TEST_FOUND=true
    fi

    if [ "$TEST_FOUND" = false ]; then
      cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: '${BASENAME}'에 대한 테스트 파일이 존재하지 않습니다. 구현 코드를 작성하기 전에 테스트를 먼저 작성하세요. (테스트 파일 예: backend/tests/test_${BASENAME}.py)"
  }
}
EOF
    fi
    exit 0
    ;;
esac

# lib/ 또는 소스 파일이면 테스트 파일 존재 여부 확인
case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.jsx)
    # 파일명 추출
    DIR=$(dirname "$FILE_PATH")
    BASENAME=$(basename "$FILE_PATH" | sed -E 's/\.(ts|tsx|js|jsx)$//')

    # 테스트 파일 후보 경로들
    TEST_FOUND=false

    # 같은 폴더에 .test 파일
    for EXT in ts tsx js jsx; do
      if [ -f "${DIR}/${BASENAME}.test.${EXT}" ] || [ -f "${DIR}/${BASENAME}.spec.${EXT}" ]; then
        TEST_FOUND=true
        break
      fi
    done

    # __tests__ 폴더
    if [ "$TEST_FOUND" = false ]; then
      PARENT=$(dirname "$DIR")
      for EXT in ts tsx js jsx; do
        if [ -f "${PARENT}/__tests__/${BASENAME}.test.${EXT}" ] || [ -f "${DIR}/__tests__/${BASENAME}.test.${EXT}" ]; then
          TEST_FOUND=true
          break
        fi
      done
    fi

    # frontend/src/__tests__/ 루트 테스트 폴더
    if [ "$TEST_FOUND" = false ]; then
      PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || echo ".")}"
      for EXT in ts tsx js jsx; do
        if [ -f "${PROJECT_ROOT}/frontend/src/__tests__/${BASENAME}.test.${EXT}" ]; then
          TEST_FOUND=true
          break
        fi
      done
    fi

    if [ "$TEST_FOUND" = false ]; then
      cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: '${BASENAME}'에 대한 테스트 파일이 존재하지 않습니다. 구현 코드를 작성하기 전에 테스트를 먼저 작성하세요. (테스트 파일 예: ${BASENAME}.test.ts)"
  }
}
EOF
    fi
    ;;
esac

exit 0
