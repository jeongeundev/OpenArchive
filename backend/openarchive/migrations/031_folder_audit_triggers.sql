-- 031_folder_audit_triggers.sql — 폴더 범위와 문서의 상속·이동 감사 (ADR-054·055)
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed'
));

CREATE FUNCTION audit_folder_visibility_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('folder_access_changed', NULL, NULL,
    jsonb_build_object('kind', 'visibility', 'folder_id', NEW.id, 'folder_name', NEW.name,
      'before', OLD.visibility, 'after', NEW.visibility));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_folder_visibility_changed
  AFTER UPDATE OF visibility ON folders FOR EACH ROW
  WHEN (OLD.visibility IS DISTINCT FROM NEW.visibility)
  EXECUTE FUNCTION audit_folder_visibility_changed();

CREATE FUNCTION audit_folder_grant_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row folder_grants%ROWTYPE;
  v_name text;
  v_grantee text;
  v_type text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  SELECT name INTO v_name FROM folders WHERE id = v_row.folder_id;
  -- 부모·부여 대상 자체의 CASCADE 삭제는 열람 부여 변경으로 기록하지 않는다.
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  IF v_row.user_id IS NOT NULL THEN
    v_type := 'user';
    SELECT username INTO v_grantee FROM users WHERE id = v_row.user_id;
  ELSE
    v_type := 'group';
    SELECT name INTO v_grantee FROM groups WHERE id = v_row.group_id;
  END IF;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('folder_access_changed', NULL, NULL,
    jsonb_build_object('kind', 'grant',
      'change', CASE WHEN TG_OP = 'DELETE' THEN 'removed' ELSE 'added' END,
      'grantee_type', v_type, 'grantee', v_grantee,
      'folder_id', v_row.folder_id, 'folder_name', v_name));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_folder_grant_changed
  AFTER INSERT OR DELETE ON folder_grants FOR EACH ROW
  EXECUTE FUNCTION audit_folder_grant_changed();

CREATE FUNCTION audit_document_folder_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_before text;
  v_after text;
BEGIN
  IF OLD.follows_folder IS DISTINCT FROM NEW.follows_folder THEN
    PERFORM audit_record('access_changed', NEW.id, NEW.title,
      jsonb_build_object('kind', 'inherit',
        'before', CASE WHEN OLD.follows_folder THEN 'folder' ELSE 'own' END,
        'after', CASE WHEN NEW.follows_folder THEN 'folder' ELSE 'own' END));
  END IF;
  IF NEW.follows_folder AND OLD.folder_id IS DISTINCT FROM NEW.folder_id THEN
    SELECT name INTO v_before FROM folders WHERE id = OLD.folder_id;
    SELECT name INTO v_after FROM folders WHERE id = NEW.folder_id;
    PERFORM audit_record('access_changed', NEW.id, NEW.title,
      jsonb_build_object('kind', 'folder', 'before', v_before, 'after', v_after));
  END IF;
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_document_folder_changed
  AFTER UPDATE OF follows_folder, folder_id ON documents FOR EACH ROW
  EXECUTE FUNCTION audit_document_folder_changed();
