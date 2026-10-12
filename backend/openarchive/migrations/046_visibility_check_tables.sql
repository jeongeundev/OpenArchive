-- 046_visibility_check_tables.sql — #193, ADR-036 트레이드오프 3 해소
-- 앱은 Literal로 막지만 psql 직접 경로(명세서 묶음 I)는 오타 값을 그대로 저장했다.
-- 그런 값은 열람 술어의 어느 분기에도 맞지 않아 소유자 외에게 조용히 사라진다.
-- 기존 행에 다른 값이 있으면 이 문장이 실패한다 — 조용히 고치지 않는다.
ALTER TABLE documents ADD CONSTRAINT documents_visibility_valid
  CHECK (visibility IN ('public', 'private'));
