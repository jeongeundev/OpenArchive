-- 017_edge_jobs_triggers.sql — ready 전이는 관계를 계산하지 않고 관계 잡을 남긴다
-- (ADR-029 결정 3 개정, #107). 큐에 종류를 들이는 스키마 변경은 016_edge_jobs_tables.sql.
--
-- 008·014에서는 이 트리거가 rebuild_document_edges를 직접 불러, 청크 교체와 같은
-- 트랜잭션에서 전 청크 HNSW 프로브가 돌았다. 실측 비용이 10청크 0.2s·159청크 2.7s라
-- 그 동안 같은 문서의 편집·태그 수정·삭제가 전부 대기했고, 판정이 실패하면 청크 교체까지
-- 롤백됐다. 여기서는 잡 행 하나만 남기고 워커가 자기 트랜잭션에서 판정한다.
--
-- 판정 본체(rebuild_document_edges)는 014 그대로다 — 워커도 `openarchive rebuild-edges`도
-- 같은 함수를 부르므로 경로마다 규칙이 갈리지 않는다.

CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'edges')
    ON CONFLICT DO NOTHING;
  PERFORM pg_notify('embedding_jobs', NEW.id::text);
  RETURN NEW;
END; $$;
