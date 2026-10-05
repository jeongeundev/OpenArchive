-- 029_audit_triggers.sql — 감사 기록의 변경·삭제 거부 (ADR-055 결정 4)
--
-- 앱 롤은 테이블 소유자라 REVOKE로는 UPDATE·DELETE를 막을 수 없다. 직접 SQL로
-- 접속한 소유자에게도 트리거가 예외를 던지며 TRUNCATE는 문 단위로 거부한다.
-- 소유자·슈퍼유저가 트리거를 끄거나 테이블을 바꾸는 것은 범위 밖이다
-- (ADR-055 트레이드오프 1).

CREATE FUNCTION audit_log_reject_change() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '감사 로그는 고치거나 지울 수 없습니다.';
END; $$;

CREATE TRIGGER trg_audit_log_reject_change
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change();

CREATE TRIGGER trg_audit_log_reject_truncate
  BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change();

-- 경로가 늘어도 기록이 빠지지 않도록 DB가 원래 쓰기와 같은 트랜잭션에서 남긴다.
-- 임베딩 잡과 같은 원칙이다 (ADR-055). 감사 INSERT 지점은 이 함수 하나다.
CREATE FUNCTION audit_record(
  p_action text, p_document_id uuid, p_title text, p_detail jsonb
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  -- HA 실측: SET LOCAL이 끝난 백엔드의 placeholder GUC는 NULL 대신 ''일 수 있다.
  v_actor text := NULLIF(current_setting('openarchive.actor_id', true), '');
  v_via text := NULLIF(current_setting('openarchive.actor_via', true), '');
  v_share text := NULLIF(current_setting('openarchive.share_id', true), '');
  v_name text;
BEGIN
  IF v_via = 'share' AND v_share IS NOT NULL THEN
    p_detail := p_detail || jsonb_build_object('share_id', v_share);
    SELECT name INTO v_name FROM shares WHERE id = v_share::uuid;
    IF FOUND THEN
      p_detail := p_detail || jsonb_build_object('share_name', v_name);
    END IF;
  END IF;
  INSERT INTO audit_log (action, actor, actor_via, document_id, document_title, detail)
  VALUES (p_action, v_actor, v_via, p_document_id, p_title, p_detail);
END; $$;

CREATE FUNCTION audit_document_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM audit_record('document_created', NEW.id, NEW.title, '{}'::jsonb);
  ELSIF TG_OP = 'DELETE' THEN
    PERFORM audit_record('document_deleted', OLD.id, OLD.title, '{}'::jsonb);
  ELSE
    PERFORM audit_record('access_changed', NEW.id, NEW.title,
      jsonb_build_object('kind', 'visibility', 'before', OLD.visibility, 'after', NEW.visibility));
  END IF;
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_document_created
  AFTER INSERT ON documents FOR EACH ROW EXECUTE FUNCTION audit_document_change();
CREATE TRIGGER trg_audit_document_deleted
  AFTER DELETE ON documents FOR EACH ROW EXECUTE FUNCTION audit_document_change();
CREATE TRIGGER trg_audit_visibility_changed
  AFTER UPDATE OF visibility ON documents FOR EACH ROW
  WHEN (OLD.visibility IS DISTINCT FROM NEW.visibility)
  EXECUTE FUNCTION audit_document_change();

CREATE FUNCTION audit_text_updated() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_title text;
BEGIN
  SELECT title INTO v_title FROM documents WHERE id = NEW.document_id;
  PERFORM audit_record('text_updated', NEW.document_id, v_title,
    jsonb_build_object('version', NEW.version));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_text_updated
  AFTER INSERT ON document_versions FOR EACH ROW WHEN (NEW.version > 1)
  EXECUTE FUNCTION audit_text_updated();

CREATE FUNCTION audit_original_replaced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_title text;
BEGIN
  SELECT title INTO v_title FROM documents WHERE id = NEW.document_id;
  PERFORM audit_record('original_replaced', NEW.document_id, v_title,
    jsonb_build_object('file_version', NEW.file_version));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_original_replaced
  AFTER INSERT ON document_files FOR EACH ROW WHEN (NEW.file_version > 1)
  EXECUTE FUNCTION audit_original_replaced();

CREATE FUNCTION audit_grant_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row document_grants%ROWTYPE;
  v_title text;
  v_grantee text;
  v_type text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  -- 외부 공유 부여는 공유 화면의 별개 축이며 열람 범위 변경으로 세지 않는다.
  IF v_row.share_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT title INTO v_title FROM documents WHERE id = v_row.document_id;
  -- CASCADE로 부모가 사라진 자식 삭제는 문서/대상 삭제이지 부여 변경이 아니다.
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  IF v_row.user_id IS NOT NULL THEN
    v_type := 'user';
    SELECT username INTO v_grantee FROM users WHERE id = v_row.user_id;
  ELSE
    v_type := 'group';
    SELECT name INTO v_grantee FROM groups WHERE id = v_row.group_id;
  END IF;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('access_changed', v_row.document_id, v_title,
    jsonb_build_object('kind', 'grant',
      'change', CASE WHEN TG_OP = 'DELETE' THEN 'removed' ELSE 'added' END,
      'grantee_type', v_type, 'grantee', v_grantee));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_grant_changed
  AFTER INSERT OR DELETE ON document_grants FOR EACH ROW
  EXECUTE FUNCTION audit_grant_changed();

CREATE FUNCTION audit_group_member_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_row group_members%ROWTYPE;
  v_group text;
  v_user text;
BEGIN
  IF TG_OP = 'DELETE' THEN v_row := OLD; ELSE v_row := NEW; END IF;
  SELECT name INTO v_group FROM groups WHERE id = v_row.group_id;
  -- 그룹·사용자 자체를 지운 CASCADE는 구성원 제거 사건으로 중복 기록하지 않는다.
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  SELECT username INTO v_user FROM users WHERE id = v_row.user_id;
  IF TG_OP = 'DELETE' AND NOT FOUND THEN RETURN NULL; END IF;
  PERFORM audit_record('group_member_changed', NULL, NULL,
    jsonb_build_object('change', CASE WHEN TG_OP = 'DELETE' THEN 'removed' ELSE 'added' END,
      'group', v_group, 'user', v_user));
  RETURN NULL;
END; $$;

CREATE TRIGGER trg_audit_group_member_changed
  AFTER INSERT OR DELETE ON group_members FOR EACH ROW
  EXECUTE FUNCTION audit_group_member_changed();

CREATE FUNCTION record_original_download(p_document_id uuid, p_file_version int)
  RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_title text;
BEGIN
  SELECT title INTO v_title FROM documents WHERE id = p_document_id;
  IF FOUND THEN
    PERFORM audit_record('original_downloaded', p_document_id, v_title,
      jsonb_build_object('file_version', p_file_version));
  END IF;
END; $$;
