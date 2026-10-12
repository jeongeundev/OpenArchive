-- 045_account_folder_audit_triggers.sql — 비밀번호·관리자 권한·폴더·사용자 토큰 감사 (ADR-055 2026-10-12 개정)
-- 관리자가 남의 폴더 이름을 바꾸거나 지워도 흔적이 없었고, 자격증명 변경이 빠져 있었다.
-- psql 직접 변경도 남도록 앱이 아니라 DB 트리거가 같은 트랜잭션에서 쓴다 (ADR-055).
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_valid;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_valid CHECK (action IN (
  'document_created', 'text_updated', 'document_deleted', 'access_changed',
  'group_member_changed', 'original_replaced', 'original_downloaded', 'folder_access_changed',
  'document_trashed', 'document_restored', 'original_previewed', 'owner_changed', 'share_changed', 'group_changed', 'user_changed',
  'password_changed', 'folder_changed', 'token_changed'
));

-- 사용자 생성에 관리자 여부를 더한다. 그룹은 041 그대로다.
CREATE OR REPLACE FUNCTION audit_principal_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row jsonb;
  v_kind text;
  v_name text;
  v_detail jsonb;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := to_jsonb(OLD); ELSE v_row := to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME = 'users' THEN
    v_kind := 'user'; v_name := v_row->>'username';
  ELSE
    v_kind := 'group'; v_name := v_row->>'name';
  END IF;
  v_detail := jsonb_build_object('change', CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'created' END,
    v_kind, v_name);
  IF v_kind = 'user' AND TG_OP = 'INSERT' THEN
    v_detail := v_detail || jsonb_build_object('is_admin', (v_row->>'is_admin')::boolean);
  END IF;
  PERFORM audit_record(v_kind || '_changed', NULL, NULL, v_detail);
  RETURN NULL;
END; $$;

-- 해시는 남기지 않는다. 본인 변경(API)과 운영자 재설정(CLI)이 같은 동작이다.
CREATE FUNCTION audit_password_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('password_changed', NULL, NULL, jsonb_build_object('user', NEW.username));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_password_changed
  AFTER UPDATE OF password_hash ON users FOR EACH ROW
  WHEN (OLD.password_hash IS DISTINCT FROM NEW.password_hash)
  EXECUTE FUNCTION audit_password_changed();

CREATE FUNCTION audit_admin_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_record('user_changed', NULL, NULL, jsonb_build_object(
    'change', CASE WHEN NEW.is_admin THEN 'admin_granted' ELSE 'admin_revoked' END,
    'user', NEW.username));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_admin_changed
  AFTER UPDATE OF is_admin ON users FOR EACH ROW
  WHEN (OLD.is_admin IS DISTINCT FROM NEW.is_admin)
  EXECUTE FUNCTION audit_admin_changed();

-- 열람 범위는 folder_access_changed(031), 소유자는 owner_changed(040)가 맡는다.
-- parent_id가 ON DELETE RESTRICT라 폴더 삭제는 연쇄되지 않는다.
CREATE FUNCTION audit_folder_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row folders%ROWTYPE;
  v_detail jsonb;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  v_detail := jsonb_build_object(
    'change', CASE TG_OP WHEN 'INSERT' THEN 'created' WHEN 'UPDATE' THEN 'renamed' ELSE 'deleted' END,
    'folder_id', v_row.id::text, 'folder_name', v_row.name, 'parent_id', v_row.parent_id::text);
  IF TG_OP = 'UPDATE' THEN
    v_detail := v_detail || jsonb_build_object('old_name', OLD.name);
  END IF;
  PERFORM audit_record('folder_changed', NULL, NULL, v_detail);
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_folder_created_deleted AFTER INSERT OR DELETE ON folders
  FOR EACH ROW EXECUTE FUNCTION audit_folder_changed();
CREATE TRIGGER trg_audit_folder_renamed
  AFTER UPDATE OF name ON folders FOR EACH ROW
  WHEN (OLD.name IS DISTINCT FROM NEW.name)
  EXECUTE FUNCTION audit_folder_changed();

-- 사용자 토큰만 맡는다 — 공유 토큰은 share_changed(041)다. 원문·해시는 넣지 않는다.
-- 계정 삭제의 연쇄 폐기는 부모가 이미 없으므로 건너뛰어 원래 삭제 사건만 남긴다 (041과 같다).
CREATE FUNCTION audit_user_token_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row api_tokens%ROWTYPE;
  v_owner text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  IF v_row.user_id IS NULL THEN RETURN NULL; END IF;
  SELECT username INTO v_owner FROM users WHERE id = v_row.user_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('token_changed', NULL, NULL, jsonb_build_object(
    'change', CASE WHEN TG_OP = 'DELETE' THEN 'revoked' ELSE 'issued' END,
    'owner', v_owner, 'token_name', v_row.name, 'scope', v_row.scope,
    'expires_at', v_row.expires_at));
  RETURN NULL;
END; $$;
CREATE TRIGGER trg_audit_user_token_changed AFTER INSERT OR DELETE ON api_tokens
  FOR EACH ROW EXECUTE FUNCTION audit_user_token_changed();
