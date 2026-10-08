-- 035_preview_audit_triggers.sql — 원본 미리보기 감사 (ADR-061 결정 7, #190)
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed',
  'document_trashed', 'document_restored', 'original_previewed'
));

CREATE FUNCTION record_original_preview(p_document_id uuid, p_file_version int)
  RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_title text;
BEGIN
  SELECT title INTO v_title FROM documents WHERE id = p_document_id;
  IF FOUND THEN
    PERFORM audit_record('original_previewed', p_document_id, v_title,
      jsonb_build_object('file_version', p_file_version));
  END IF;
END; $$;
