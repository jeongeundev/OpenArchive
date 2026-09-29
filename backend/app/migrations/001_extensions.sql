-- 001_extensions.sql — 확장 설치
--
-- pgvector: vector 타입과 거리 연산자(<=>)를 제공한다. 이 프로젝트가 DB에 요구하는
-- 유일한 확장이다. OpenSQL v3 배포판에 0.8.1이 번들되어 있고, 로컬 개발 컨테이너
-- pgvector/pgvector:pg17도 같은 계열이다 (OPENSQL_RESEARCH.md §0).
--
-- 여기에 넣지 않는 것:
--   pgcrypto      — gen_random_uuid()는 PostgreSQL 13부터 코어에 있다. 불필요하다.
--   pgvectorscale — 번들되어 있으나 채택하지 않았다. 증분 삽입에 강한 HNSW를 쓴다 (ADR-002).
--   pg_trgm       — ADR-016(키워드 결합)은 조건부이며 이 확장에 의존하지 않는다.

CREATE EXTENSION IF NOT EXISTS vector;
