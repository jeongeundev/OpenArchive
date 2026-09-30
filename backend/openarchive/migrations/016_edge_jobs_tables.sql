-- 016_edge_jobs_tables.sql — 잡 큐에 종류를 들인다 (ADR-029 결정 3 개정, #107)
--
-- ready 전이에서 관계를 계산하는 대신 관계 잡을 기록하려면 큐가 두 종류를 담아야 한다.
-- 별도 테이블 대신 kind로 구분해 claim·fail·release·sweep·CASCADE·좀비 임계를 공유한다.
-- 한 문서에 임베딩 잡과 관계 잡이 동시에 대기할 수 있도록 코얼레싱 키에도 kind를 넣는다.
-- 트리거 쪽 변경은 017_edge_jobs_triggers.sql에 있다.
--
-- 012→015와 같이 새 파일로 둔다: 러너가 파일명으로 적용 이력을 남기므로 기존 파일을
-- 고치면 이미 마이그레이션을 적용한 DB에는 변경이 반영되지 않는다.

ALTER TABLE embedding_jobs ADD COLUMN kind text NOT NULL DEFAULT 'embed';
ALTER TABLE embedding_jobs ADD CONSTRAINT embedding_jobs_kind_valid
  CHECK (kind IN ('embed', 'edges'));

-- 002_tables.sql의 코얼레싱 인덱스를 (문서, 종류) 단위로 교체한다. 여기(=성능 인덱스를
-- 모으는 004가 아니라 테이블 파일)에 두는 이유는 002와 같다 — 이것은 데이터 무결성
-- 제약이다. kind가 키에 없으면 ready 전이의 관계 잡 INSERT가 같은 문서의 pending 임베딩
-- 잡과 충돌해 ON CONFLICT DO NOTHING으로 조용히 사라지고, 그 문서의 관계는 아무도
-- 계산하지 않는다.
DROP INDEX uq_pending_job_per_doc;
CREATE UNIQUE INDEX uq_pending_job_per_doc_kind
  ON embedding_jobs(document_id, kind) WHERE status = 'pending';
