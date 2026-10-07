-- 033_trash_audit_triggers.sql — 휴지통 이동·복원 감사 (ADR-060 결정 5)
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed',
  'document_trashed', 'document_restored'
));

CREATE FUNCTION audit_document_trash_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- WHEN이 NULL↔값 전이만 통과시키므로 새 값으로 방향을 구분한다.
  IF NEW.deleted_at IS NOT NULL THEN
    PERFORM audit_record('document_trashed', NEW.id, NEW.title, '{}'::jsonb);
  ELSE
    PERFORM audit_record('document_restored', NEW.id, NEW.title, '{}'::jsonb);
  END IF;
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_document_trash_changed
  AFTER UPDATE OF deleted_at ON documents FOR EACH ROW
  WHEN ((OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL))
  EXECUTE FUNCTION audit_document_trash_changed();
