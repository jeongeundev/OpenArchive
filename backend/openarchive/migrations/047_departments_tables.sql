-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- D14: 소속 부서는 기존 그룹 하나이며, 여러 그룹의 구성원 자격과 열람 규칙은 그대로다.
-- D15: 부서 폴더는 최상위만 지정하고, 한 부서에 하나만 둔다.
ALTER TABLE users ADD COLUMN department_group_id uuid REFERENCES groups(id) ON DELETE SET NULL;
ALTER TABLE folders ADD COLUMN department_group_id uuid REFERENCES groups(id) ON DELETE SET NULL;
ALTER TABLE folders ADD CONSTRAINT folders_department_root
  CHECK (department_group_id IS NULL OR parent_id IS NULL);
CREATE UNIQUE INDEX uq_folders_department
  ON folders (department_group_id) WHERE department_group_id IS NOT NULL;
