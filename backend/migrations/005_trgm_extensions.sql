-- 005_trgm_extensions.sql — 관계 방향 판정과 RRF 검색이 공유하는 문자열 유사도 확장
--
-- m7의 broader 방향 판정이 비대칭 word_similarity()에 의존하므로 관계 생성보다 먼저
-- 설치한다. m9의 키워드 + 벡터 RRF 검색도 별도 확장을 추가하지 않고 같은 pg_trgm을
-- 재사용한다. pg_trgm 1.6은 로컬 pgvector 컨테이너와 실 OpenSQL VM 양쪽에서 확인됐다
-- (OPENSQL_RESEARCH.md §14, ADR-026).
--
-- 적용 여부와 멱등성은 schema_migrations가 담당하므로 IF NOT EXISTS를 쓰지 않는다
-- (ADR-005). 확장이 없는 환경을 조용히 통과시키면 관계 판정이 뒤늦게 실패한다.

CREATE EXTENSION pg_trgm;
