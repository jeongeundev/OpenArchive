#!/bin/sh
# 앱 이미지 진입점 (#95-e2, ADR-039 개정).
#
# 인자가 없으면 init → serve. init은 다시 실행해도 되는 명령이라 기동마다 부른다 —
# 첫 기동에는 스키마와 첫 관리자(ADMIN_PASSWORD)를 만들고, 이후에는 "이미 최신"으로
# 지나간다. 충돌 테이블·확장 부족은 API 트레이스백 대신 init의 안내로 멈춘다.
# 인자가 있으면 그 명령만 실행한다 (예: openarchive create-user bob).
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

if [ -z "$DATABASE_URL" ]; then
    echo "DATABASE_URL이 필요합니다 — docker run -e DATABASE_URL=postgresql://… 로 주십시오." >&2
    exit 2
fi

openarchive init --yes --dsn "$DATABASE_URL"
# exec — serve가 PID 1이 되어야 docker stop의 SIGTERM이 워커 정리까지 닿는다.
exec openarchive serve --host 0.0.0.0 --port 8000
