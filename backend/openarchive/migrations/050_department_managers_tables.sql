-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- #296, ADR-066: 부서 설명과 구성원 관리 위임. 부서 관리자는 반드시 그 부서 구성원이다.
ALTER TABLE groups ADD COLUMN description text NOT NULL DEFAULT '';
ALTER TABLE groups ADD CONSTRAINT groups_description_length
  CHECK (char_length(description) <= 200);

CREATE TABLE group_managers (
  group_id   uuid NOT NULL,
  user_id    uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (group_id, user_id),
  FOREIGN KEY (group_id, user_id)
    REFERENCES group_members (group_id, user_id) ON DELETE CASCADE
);
CREATE INDEX idx_group_managers_user ON group_managers (user_id);
