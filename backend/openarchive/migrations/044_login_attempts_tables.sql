-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- 044_login_attempts_tables.sql — 로그인 실패 제한 (ADR-040 2026-10-12 개정)
-- 사용자명 기준이고 users FK가 없다: 없는 사용자명도 똑같이 세고 잠가, 잠금 여부로
-- 계정이 있는지 알 수 없게 한다. 카운터를 DB에 두므로 API 재시작·다중 프로세스에도 유지된다.
CREATE TABLE login_attempts (
  username     text PRIMARY KEY,
  failures     integer NOT NULL DEFAULT 0 CHECK (failures >= 0),
  locked_until timestamptz
);
