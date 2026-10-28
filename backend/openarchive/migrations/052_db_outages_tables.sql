-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- #300: API 프로세스가 관측한 DB 장애와 복구 구간.
CREATE TABLE db_outages (
  id                   bigserial PRIMARY KEY,
  kind                 text NOT NULL CHECK (kind IN ('promotion', 'interruption')),
  last_ok_at           timestamptz NOT NULL,
  first_failure_at     timestamptz NOT NULL,
  recovered_at         timestamptz NOT NULL,
  node_before          text,
  node_after           text,
  timeline_before      integer,
  timeline_after       integer,
  failed_checks        integer NOT NULL CHECK (failed_checks >= 0),
  requests_retried     integer NOT NULL DEFAULT 0,
  requests_unavailable integer NOT NULL DEFAULT 0,
  server_errors        integer NOT NULL DEFAULT 0,
  recorded_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (last_ok_at <= first_failure_at AND first_failure_at <= recovered_at)
);
COMMENT ON TABLE db_outages IS '앱 API 프로세스가 관측해 쓰는 DB 장애 기록 — 감사 로그와 별개';
