-- 041_share_principal_audit_triggers.sql — ADR-061 결정 4, #201
-- 부모가 이미 없는 연쇄 삭제는 건너뛰어 원래 삭제 사건만 기록한다 (ADR-055).
-- 공유는 조직 밖으로 여는 별개 축이라 부여도 access_changed 대신 share_changed다.
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed',
  'document_trashed', 'document_restored', 'original_previewed', 'owner_changed', 'share_changed', 'group_changed', 'user_changed'
));


-- 삭제된 공유는 OLD 스냅샷을 전달하고, 자식은 현재 부모 행을 전달한다.
CREATE FUNCTION audit_share_detail(p_share shares, p_change text) RETURNS jsonb
LANGUAGE sql AS $$
  SELECT jsonb_build_object('change', p_change, 'share_id', p_share.id::text,
    'share_name', p_share.name, 'owner', username)
  FROM users WHERE id = p_share.owner_user_id;
$$;

CREATE FUNCTION audit_share_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_share shares%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN v_share := OLD; ELSE v_share := NEW; END IF;
  IF TG_OP = 'DELETE' AND NOT EXISTS (
    SELECT 1 FROM users WHERE id = v_share.owner_user_id
  ) THEN RETURN NULL; END IF;
  PERFORM audit_record('share_changed', NULL, NULL, audit_share_detail(v_share,
    CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'created' END));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_share_changed AFTER INSERT OR DELETE ON shares
  FOR EACH ROW EXECUTE FUNCTION audit_share_changed();

CREATE FUNCTION audit_share_grant_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row document_grants%ROWTYPE;
  v_share shares%ROWTYPE;
  v_title text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  IF v_row.share_id IS NULL THEN RETURN NULL; END IF;
  SELECT s.* INTO v_share FROM shares s JOIN users u ON u.id = s.owner_user_id
    WHERE s.id = v_row.share_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT title INTO v_title FROM documents WHERE id = v_row.document_id;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('share_changed', v_row.document_id, v_title,
    audit_share_detail(v_share,
      CASE WHEN TG_OP = 'DELETE' THEN 'document_removed' ELSE 'document_added' END));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_share_grant_changed AFTER INSERT OR DELETE ON document_grants
  FOR EACH ROW EXECUTE FUNCTION audit_share_grant_changed();

CREATE FUNCTION audit_share_token_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row api_tokens%ROWTYPE;
  v_share shares%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  IF v_row.share_id IS NULL THEN RETURN NULL; END IF;
  SELECT s.* INTO v_share FROM shares s JOIN users u ON u.id = s.owner_user_id
    WHERE s.id = v_row.share_id;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('share_changed', NULL, NULL,
    audit_share_detail(v_share,
      CASE WHEN TG_OP = 'DELETE' THEN 'token_revoked' ELSE 'token_issued' END)
      || jsonb_build_object('token_name', v_row.name));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_share_token_changed AFTER INSERT OR DELETE ON api_tokens
  FOR EACH ROW EXECUTE FUNCTION audit_share_token_changed();

CREATE FUNCTION audit_principal_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row jsonb;
  v_kind text;
  v_name text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := to_jsonb(OLD); ELSE v_row := to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME = 'users' THEN
    v_kind := 'user'; v_name := v_row->>'username';
  ELSE
    v_kind := 'group'; v_name := v_row->>'name';
  END IF;
  PERFORM audit_record(v_kind || '_changed', NULL, NULL,
    jsonb_build_object('change', CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'created' END,
      v_kind, v_name));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_user_changed AFTER INSERT OR DELETE ON users
  FOR EACH ROW EXECUTE FUNCTION audit_principal_changed();
CREATE TRIGGER trg_audit_group_changed AFTER INSERT OR DELETE ON groups
  FOR EACH ROW EXECUTE FUNCTION audit_principal_changed();
