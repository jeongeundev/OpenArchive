-- 030_folders_tables.sql — 폴더 트리와 열람 부여 (ADR-054, #187)
--
-- 범위는 최상위 폴더만 갖고 하위 폴더는 상속한다 (D1). 폴더 이동은 제공하지 않아
-- 만든 뒤 최상위 조상은 바뀌지 않는다 (D2). 실효 범위는 저장하지 않고 조회 때 판정한다.
-- 최상위 이름은 유일하게 하지 않는다 — 남의 제한 폴더와 이름이 충돌한다는 오류도
-- 그 폴더의 존재를 누출한다. 같은 부모 아래의 하위 폴더만 이름이 유일하다.

CREATE TABLE folders (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id  uuid REFERENCES folders(id) ON DELETE RESTRICT,
  name       text NOT NULL,
  created_by text NOT NULL,
  visibility text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT folders_name_valid
    CHECK (length(btrim(name, E' \t\r\n\f')) > 0 AND position('/' in name) = 0),
  CONSTRAINT folders_visibility_valid CHECK (visibility IN ('public', 'private')),
  CONSTRAINT folders_root_scope CHECK ((parent_id IS NULL) = (visibility IS NOT NULL))
);

CREATE UNIQUE INDEX uq_folders_parent_name
  ON folders (parent_id, name) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_folders_parent ON folders (parent_id);

-- 사용자·그룹 부여만 둔다. 외부 공유 주체는 폴더를 보지 않는다 (ADR-054).
CREATE TABLE folder_grants (
  folder_id  uuid NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
  group_id   uuid REFERENCES groups(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT folder_grants_one_grantee CHECK (num_nonnulls(user_id, group_id) = 1)
);

CREATE UNIQUE INDEX uq_folder_grants_user
  ON folder_grants (folder_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_folder_grants_group
  ON folder_grants (folder_id, group_id) WHERE group_id IS NOT NULL;
CREATE INDEX idx_folder_grants_folder ON folder_grants (folder_id);
CREATE INDEX idx_folder_grants_user ON folder_grants (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_folder_grants_group ON folder_grants (group_id) WHERE group_id IS NOT NULL;

-- 기존 문서는 folder_id NULL이므로 문서 자신의 범위를 그대로 쓴다. 데이터 이전은 없다.
ALTER TABLE documents
  ADD COLUMN folder_id uuid REFERENCES folders(id) ON DELETE RESTRICT,
  ADD COLUMN follows_folder boolean NOT NULL DEFAULT true;
CREATE INDEX idx_documents_folder ON documents (folder_id);
