-- 034_token_expiry_tables.sql — 토큰 만료일·마지막 사용 시각 (ADR-061 결정 1, #199)
--
-- 사용자 토큰과 공유 토큰에 공통으로 적용한다. NULL 만료일은 만료 없음이며,
-- 기본값을 두지 않아 기존 발급 SQL과 기존 토큰을 그대로 유지한다.
-- 과거 만료일 거부는 발급 서비스가 담당한다. 테스트·실측에서 UPDATE로 만료를
-- 앞당길 수 있도록 created_at과 비교하는 CHECK는 두지 않는다.
-- last_used_at은 validate_token이 인증 성공 시 1분 단위로 갱신한다.

ALTER TABLE api_tokens
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN last_used_at timestamptz;
