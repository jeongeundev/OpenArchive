-- 005_trgm_extensions.sql — 관계 방향 판정과 RRF 검색이 공유하는 문자열 유사도 확장
--
-- 애초에 m7의 broader 방향 판정이 비대칭 word_similarity()에 의존해 관계 생성보다 먼저
-- 설치했다. 그 판정은 §14에서 기각됐고(정답쌍에서도 방향 차가 벌어지지 않았다) 008이
-- broader를 제거했으므로, **현재 이 확장을 쓰는 애플리케이션 코드는 없다.** 남는 근거는
-- m9의 키워드 + 벡터 RRF 검색이 별도 확장을 추가하지 않고 같은 pg_trgm을 재사용한다는
-- 것과, §14의 방향 차 측정을 재현하려면 확장이 필요하다는 것 둘이다. m9가 잘리면 이
-- 마이그레이션은 회수 대상이다. pg_trgm 1.6은 로컬 pgvector 컨테이너와 실 OpenSQL VM
-- 양쪽에서 확인됐다 (OPENSQL_RESEARCH.md §14, ADR-026).
--
-- 적용 여부와 멱등성은 schema_migrations가 담당하므로 IF NOT EXISTS를 쓰지 않는다
-- (ADR-005). 확장이 없는 환경을 조용히 통과시키면 관계 판정이 뒤늦게 실패한다.

CREATE EXTENSION pg_trgm;
