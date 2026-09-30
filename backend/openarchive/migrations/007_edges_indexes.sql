-- 007_edges_indexes.sql — 관계 그래프 순회와 재계산 삭제 경로
--
-- 정방향 순회는 src, 역방향 순회와 백링크는 dst에서 시작한다. 관계 재계산 트리거도
-- `WHERE src_document_id = ? OR dst_document_id = ?`로 기존 edge를 지우므로 두 인덱스가
-- 삭제 경로를 함께 지원한다. kind는 특정 관계만 순회할 때 같은 인덱스에서 거른다.

CREATE INDEX idx_document_edges_src_kind
  ON document_edges (src_document_id, kind);

CREATE INDEX idx_document_edges_dst_kind
  ON document_edges (dst_document_id, kind);
