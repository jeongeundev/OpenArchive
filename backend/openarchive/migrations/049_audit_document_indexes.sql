-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- 049_audit_document_indexes.sql — 문서별 활동 기록 조회 인덱스 (#293)
-- 문서 상세 활동 기록이 `WHERE document_id = $1 ORDER BY id DESC LIMIT n`으로 읽는다.
-- 028의 인덱스는 시각·행위자·동작 기준이라 이 조회를 받지 못한다.
CREATE INDEX idx_audit_log_document ON audit_log (document_id, id DESC);
