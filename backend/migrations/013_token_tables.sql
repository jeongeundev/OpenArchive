-- 013_token_tables.sql — 프로그램용 위임 토큰
--
-- API 토큰은 사람 계정이 프로그램에 위임하는 장수명 자격증명이다. 원문은 CI·스크립트
-- 설정에 남을 수 있으므로 DB에는 sha256 해시만 저장해, DB 덤프만으로 인증할 수 없게 한다.
-- 토큰은 고엔트로피 난수라 salt나 비용 해시 없이도 인덱스로 직접 찾을 수 있다.

CREATE TABLE api_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       text NOT NULL,          -- 여러 토큰 중 독립 폐기할 자격증명을 식별한다
  token_hash text NOT NULL UNIQUE,   -- sha256(원문). 원문은 어디에도 저장하지 않는다
  scope      text NOT NULL CHECK (scope IN ('read', 'read_write')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 만료·폐기 상태를 따로 저장하지 않는다. 장수명 토큰의 폐기는 이 행을 삭제하는 것이며,
-- 계정을 삭제하면 ON DELETE CASCADE가 그 계정이 발급한 모든 자격증명을 함께 폐기한다.
