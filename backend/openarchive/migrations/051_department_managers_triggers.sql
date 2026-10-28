-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- #296, ADR-066: 부서 관리자·설명 변경도 기존 group_changed 감사로 남긴다.
CREATE FUNCTION audit_group_manager_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row group_managers%ROWTYPE;
  v_group text;
  v_user text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  SELECT name INTO v_group FROM groups WHERE id = v_row.group_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT username INTO v_user FROM users WHERE id = v_row.user_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('group_changed', NULL, NULL, jsonb_build_object(
    'change', CASE WHEN TG_OP = 'DELETE' THEN 'manager_removed' ELSE 'manager_added' END,
    'group', v_group, 'user', v_user));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_group_manager_changed
  AFTER INSERT OR DELETE ON group_managers
  FOR EACH ROW EXECUTE FUNCTION audit_group_manager_changed();

CREATE FUNCTION audit_group_description_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('group_changed', NULL, NULL, jsonb_build_object(
    'change', 'description_changed', 'group', NEW.name));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_group_description_changed
  AFTER UPDATE OF description ON groups FOR EACH ROW
  WHEN (OLD.description IS DISTINCT FROM NEW.description)
  EXECUTE FUNCTION audit_group_description_changed();
