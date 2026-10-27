-- Copyright 2026 jeongeundev
-- SPDX-License-Identifier: MIT
-- D14: 소속 부서는 그룹 구성원이어야 하며, 탈퇴하면 같은 트랜잭션에서 비운다.
-- D18: 직접 SQL·자동 해제도 기존 감사 동작으로 기록한다.
CREATE FUNCTION ensure_department_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.department_group_id IS NOT NULL THEN
    PERFORM 1 FROM group_members
      WHERE user_id = NEW.id AND group_id = NEW.department_group_id
      FOR KEY SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION '소속 부서의 그룹 구성원이어야 합니다.';
    END IF;
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER trg_users_department_membership
  BEFORE INSERT OR UPDATE OF department_group_id ON users
  FOR EACH ROW EXECUTE FUNCTION ensure_department_membership();

CREATE FUNCTION clear_department_on_member_removed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE users SET department_group_id = NULL
    WHERE id = OLD.user_id AND department_group_id = OLD.group_id;
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_group_members_department_removed
  AFTER DELETE ON group_members
  FOR EACH ROW EXECUTE FUNCTION clear_department_on_member_removed();

CREATE FUNCTION audit_department_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_from text;
  v_to text;
BEGIN
  SELECT name INTO v_from FROM groups WHERE id = OLD.department_group_id;
  SELECT name INTO v_to FROM groups WHERE id = NEW.department_group_id;
  PERFORM audit_record('user_changed', NULL, NULL, jsonb_build_object(
    'change', 'department_changed', 'user', NEW.username, 'from', v_from, 'to', v_to));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_department_changed
  AFTER UPDATE OF department_group_id ON users FOR EACH ROW
  WHEN (OLD.department_group_id IS DISTINCT FROM NEW.department_group_id)
  EXECUTE FUNCTION audit_department_changed();

CREATE FUNCTION audit_folder_department_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_department text;
BEGIN
  SELECT name INTO v_department FROM groups
    WHERE id = COALESCE(NEW.department_group_id, OLD.department_group_id);
  PERFORM audit_record('folder_changed', NULL, NULL, jsonb_build_object(
    'change', CASE WHEN NEW.department_group_id IS NULL
      THEN 'department_cleared' ELSE 'department_set' END,
    'folder_id', NEW.id::text, 'folder_name', NEW.name, 'department', v_department));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_folder_department_changed
  AFTER UPDATE OF department_group_id ON folders FOR EACH ROW
  WHEN (OLD.department_group_id IS DISTINCT FROM NEW.department_group_id)
  EXECUTE FUNCTION audit_folder_department_changed();
