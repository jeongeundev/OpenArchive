-- 043_share_folders_audit_triggers.sql — 폴더 공유 감사 (#206)
-- 부모가 이미 없는 연쇄 삭제는 건너뛴다 (041, ADR-055).
CREATE FUNCTION audit_share_folder_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row share_folders%ROWTYPE;
  v_share shares%ROWTYPE;
  v_folder_name text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  SELECT s.* INTO v_share FROM shares s JOIN users u ON u.id = s.owner_user_id
    WHERE s.id = v_row.share_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT name INTO v_folder_name FROM folders WHERE id = v_row.folder_id;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('share_changed', NULL, NULL,
    audit_share_detail(v_share,
      CASE WHEN TG_OP = 'DELETE' THEN 'folder_removed' ELSE 'folder_added' END)
      || jsonb_build_object('folder_id', v_row.folder_id::text, 'folder_name', v_folder_name));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_share_folder_changed AFTER INSERT OR DELETE ON share_folders
  FOR EACH ROW EXECUTE FUNCTION audit_share_folder_changed();
