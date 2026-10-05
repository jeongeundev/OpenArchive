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
