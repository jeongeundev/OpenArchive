-- 025_grants_tables.sql — 그룹과 문서 열람 부여 (ADR-044, #97)
--
-- private 문서는 소유자와 부여 대상이 본다. public 문서는 지금처럼 조직(이 설치에
-- 로그인한 사용자) 전체가 본다. 값은 그대로 두고 private의 의미만 "소유자 + 부여"로
-- 넓힌다 — 이미 게시된 API·MCP·CLI의 visibility 계약을 깨지 않기 위해서다.
--
-- 부여는 읽기다. 편집은 지금처럼 소유자만 한다 (ADR-044 기각한 대안 3).
--
-- 앱이 직접 INSERT한다: 잡·관계를 트리거에 둔 규칙은 원본-벡터 정합성의 파생물에 대한
-- 것이고, 부여는 파생물이 아니라 사람이 내린 결정의 기록이다.

-- 그룹은 사용자의 집합(부서·팀)이며 관리자가 만든다.
CREATE TABLE groups (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
  group_id uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);

-- 열람 술어가 "이 사용자가 속한 그룹"을 찾는 방향이다. 기본키는 group_id가 앞이라 못 쓴다.
CREATE INDEX idx_group_members_user ON group_members (user_id);

-- 대상을 다형 칼럼(principal_kind, principal_id) 하나로 두지 않고 종류별 칼럼으로 둔다.
-- 다형 칼럼에는 FK를 걸 수 없어, 사용자·그룹을 지워도 아무도 가리키지 않는 부여가 남는다.
-- 공유(share) 대상은 공유 주체가 생길 때 칼럼과 CHECK를 함께 넓힌다 (#97 c).
CREATE TABLE document_grants (
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
  group_id    uuid REFERENCES groups(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_grants_one_grantee CHECK (num_nonnulls(user_id, group_id) = 1)
);

-- 같은 부여 두 줄을 막는다. UNIQUE NULLS NOT DISTINCT는 PostgreSQL 15부터라 쓰지 않는다 —
-- 설치 대상에 14가 있다. 대상 종류마다 부분 유니크 인덱스로 같은 효과를 낸다.
CREATE UNIQUE INDEX uq_document_grants_user
  ON document_grants (document_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_document_grants_group
  ON document_grants (document_id, group_id) WHERE group_id IS NOT NULL;

-- 열람 술어는 검색 후보 행마다 "이 문서의 부여"를 찾는다. 위 둘은 부분 인덱스라 대상
-- 종류 조건이 없는 이 조회에 쓰이지 않는다 — 없으면 후보마다 부여 전체를 훑는다.
CREATE INDEX idx_document_grants_document ON document_grants (document_id);
