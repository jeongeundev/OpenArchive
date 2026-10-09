-- 040_owner_audit_triggers.sql — 소유권 이전 감사 (ADR-061 결정 2, #200)
-- 문서·폴더 소유자 변경은 owner_changed 한 동작으로 기록한다 (D5).
-- 직접 SQL 경로에서도 빠지지 않도록 앱이 아니라 DB 트리거가 같은 트랜잭션에서 쓴다 (ADR-055).
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed',
  'document_trashed', 'document_restored', 'original_previewed', 'owner_changed'
));

CREATE FUNCTION audit_document_owner_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('owner_changed', NEW.id, NEW.title,
    jsonb_build_object('kind', 'document', 'before', OLD.owner_id, 'after', NEW.owner_id));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_document_owner_changed
  AFTER UPDATE OF owner_id ON documents FOR EACH ROW
  WHEN (OLD.owner_id IS DISTINCT FROM NEW.owner_id)
  EXECUTE FUNCTION audit_document_owner_changed();

CREATE FUNCTION audit_folder_owner_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('owner_changed', NULL, NULL,
    jsonb_build_object('kind', 'folder', 'folder_id', NEW.id, 'folder_name', NEW.name,
      'before', OLD.created_by, 'after', NEW.created_by));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_folder_owner_changed
  AFTER UPDATE OF created_by ON folders FOR EACH ROW
  WHEN (OLD.created_by IS DISTINCT FROM NEW.created_by)
  EXECUTE FUNCTION audit_folder_owner_changed();
