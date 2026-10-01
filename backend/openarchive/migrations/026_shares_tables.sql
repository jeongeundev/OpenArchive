-- 026_shares_tables.sql — 외부 협업용 공유 주체와 읽기 전용 토큰 (ADR-044, #97 c)
--
-- 공유는 소유자가 지정한 문서 집합을 읽는 주체다. 공유를 지우면 부여와 토큰도
-- 함께 사라지며, 사용자 토큰과 공유 토큰은 정확히 한 주체에 귀속된다.
-- 공유 부여도 앱이 직접 INSERT한다: 파이프라인 파생물이 아니라 사람이 내린
-- 결정의 기록이므로 잡·관계를 DB 트리거가 만드는 규칙과 구분한다 (025).

CREATE TABLE shares (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (owner_user_id, name)
);

ALTER TABLE document_grants
  ADD COLUMN share_id uuid REFERENCES shares(id) ON DELETE CASCADE,
  DROP CONSTRAINT document_grants_one_grantee,
  ADD CONSTRAINT document_grants_one_grantee
    CHECK (num_nonnulls(user_id, group_id, share_id) = 1);

CREATE UNIQUE INDEX uq_document_grants_share
  ON document_grants (document_id, share_id) WHERE share_id IS NOT NULL;
-- 공유 화면에서 이 공유에 포함된 문서 목록을 찾는다.
CREATE INDEX idx_document_grants_share
  ON document_grants (share_id) WHERE share_id IS NOT NULL;

ALTER TABLE api_tokens
  ALTER COLUMN user_id DROP NOT NULL,
  ADD COLUMN share_id uuid REFERENCES shares(id) ON DELETE CASCADE,
  ADD CONSTRAINT api_tokens_one_principal CHECK (num_nonnulls(user_id, share_id) = 1),
  ADD CONSTRAINT api_tokens_share_read_only CHECK (share_id IS NULL OR scope = 'read');

-- 주체 값 share:<uuid>와 사용자명이 충돌하지 않도록 접두사를 예약한다 (ADR-044 공유 결정 3).
ALTER TABLE users
  ADD CONSTRAINT users_reserved_share_prefix CHECK (username NOT LIKE 'share:%');
