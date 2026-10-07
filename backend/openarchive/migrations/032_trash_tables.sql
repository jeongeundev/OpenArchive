-- 032_trash_tables.sql — 휴지통 이동 시각과 조회 인덱스 (ADR-060 결정 1·2)
--
-- 삭제는 시각을 기록하는 UPDATE로 처리하고, 청크·관계는 그대로 둬 복원 때 재계산하지 않는다.
-- 휴지통 문서를 제외하는 조건은 열람 술어 한 곳에서 처리한다.

ALTER TABLE documents ADD COLUMN deleted_at timestamptz NULL;

CREATE INDEX idx_documents_trash
  ON documents (owner_id, deleted_at) WHERE deleted_at IS NOT NULL;
