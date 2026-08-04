-- 004_indexes.sql — 청크 임베딩 벡터 인덱스
--
-- HNSW를 쓴다 (ADR-002). IVFFlat은 인덱스 생성 시점에 데이터가 있어야 클러스터 품질이
-- 확보되는데, 이 플랫폼은 문서가 계속 유입되는 시나리오라 증분 삽입에 강한 HNSW가 맞다.
-- pgvectorscale(StreamingDiskANN)도 배포판에 번들되어 있으나, 데모 규모에서 HNSW 대비
-- 우위가 불분명하고 의존성이 늘어난다 — "쓸 수 없어서"가 아니라 "쓸 수 있지만 고르지
-- 않는" 것이다.
--
-- `vector_cosine_ops`여야 검색 쿼리의 `<=>` 정렬이 이 인덱스를 탄다. L2로 만들면 인덱스는
-- 정상 생성되지만 검색이 조용히 풀스캔으로 떨어진다. BGE-M3의 정규화 임베딩과도 맞다.
--
-- `m`·`ef_construction`은 기본값(16 / 64)을 쓰고 명시하지 않는다. 튜닝 근거가 아직 없고,
-- 근거 없는 숫자를 적어두면 나중에 바꿀 때 그 값이 의도적이었는지 알 수 없게 된다.
--
-- 여기에 없는 것:
--   `documents(embedding_status)`·`embedding_jobs(status, next_attempt_at)` 등 조회용 보조
--   인덱스 — 데모 규모에서 필요가 입증되지 않았다. 워커의 claim 쿼리는 `LIMIT 1`이고 큐
--   길이도 짧다. 실측으로 느린 것이 확인되면 그때 근거와 함께 추가한다.
--
--   파셜 유니크 인덱스 `uq_pending_job_per_doc` — 002_tables.sql에 있다. 성능 인덱스가
--   아니라 코얼레싱을 보장하는 무결성 제약이라 테이블 정의와 함께 두었다.
--
-- 검색 시 recall 보강은 인덱스 정의가 아니라 질의 쪽에서 한다 —
-- 트랜잭션 안에서 `SET LOCAL hnsw.ef_search = 200` (ADR-011). `hnsw.iterative_scan`은
-- pgvector 0.8+에서 쓸 수 있으나 실측 전까지 켜지 않는다 (ADR-011 보강 3).

CREATE INDEX idx_chunks_embedding ON document_chunks
  USING hnsw (embedding vector_cosine_ops);
