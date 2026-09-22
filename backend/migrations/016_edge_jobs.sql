-- 016_edge_jobs.sql — ready 전이에서는 관계 계산 대신 관계 잡을 기록한다.
-- 별도 테이블 대신 kind로 구분해 claim·fail·release·sweep·CASCADE·좀비 임계를 공유한다.
-- 한 문서에 임베딩 잡과 관계 잡이 동시에 대기할 수 있도록 코얼레싱 키에도 kind를 넣는다.
-- 012→015와 같이 새 파일로 둔다: 러너가 파일명으로 적용 이력을 남기므로 기존 파일을
-- 고치면 이미 마이그레이션을 적용한 DB에는 변경이 반영되지 않는다.

ALTER TABLE embedding_jobs ADD COLUMN kind text NOT NULL DEFAULT 'embed';
ALTER TABLE embedding_jobs ADD CONSTRAINT embedding_jobs_kind_valid
  CHECK (kind IN ('embed', 'edges'));

DROP INDEX uq_pending_job_per_doc;
CREATE UNIQUE INDEX uq_pending_job_per_doc_kind
  ON embedding_jobs(document_id, kind) WHERE status = 'pending';

CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'edges')
    ON CONFLICT DO NOTHING;
  PERFORM pg_notify('embedding_jobs', NEW.id::text);
  RETURN NEW;
END; $$;
