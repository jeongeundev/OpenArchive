-- 028_audit_tables.sql — 사건 시점의 감사 기록 (ADR-055 결정 2·4·5)
--
-- 기록은 대상 테이블의 트리거·DB 함수가 원래 작업과 같은 트랜잭션에서 남긴다.
-- 앱은 audit_log에 직접 INSERT하지 않는다. 기록을 고치거나 지우는 것은 029가 거부한다.
-- 문서·사용자가 삭제돼도 남도록 document_id에는 FK를 걸지 않고 제목·사용자명을
-- 스냅샷으로 둔다. db_role은 모든 행에 DB 롤을 남겨 앱 경로와 직접 SQL 접속을
-- 구분한다 — pgaudit처럼 DB 접속 주체와 앱 행위자를 함께 확인하기 위해서다.

CREATE TABLE audit_log (
  id             bigserial PRIMARY KEY,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  action         text NOT NULL,
  actor          text,
  actor_via      text,
  db_role        text NOT NULL DEFAULT current_user,
  document_id    uuid,
  document_title text,
  detail         jsonb NOT NULL DEFAULT '{}',
  CONSTRAINT audit_log_action_valid CHECK (action IN (
    'document_created', 'text_updated', 'document_deleted', 'access_changed',
    'group_member_changed', 'original_replaced', 'original_downloaded'
  )),
  CONSTRAINT audit_log_actor_via_valid CHECK (actor_via IN (
    'session', 'token', 'mcp', 'cli', 'share', 'worker'
  ))
);

-- 관리 화면의 최신순·사용자·동작 필터와 id 커서 조회를 지원한다.
CREATE INDEX idx_audit_log_occurred_at ON audit_log (occurred_at DESC, id DESC);
CREATE INDEX idx_audit_log_actor ON audit_log (actor, id DESC);
CREATE INDEX idx_audit_log_action ON audit_log (action, id DESC);
