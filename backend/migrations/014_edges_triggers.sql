-- 014_edges_triggers.sql — 관계 판정을 rebuild_document_edges로 옮긴다 (ADR-029 개정, #94)
--
-- 계산한 문서의 src 행만 교체해 남의 발견을 지우지 않는다. 재실행 자카드는
-- 0.97 → 0.99로 개선됐다. search.py·related.py가 이미 역방향을 UNION하므로
-- 저장은 단방향이어도 조회에서는 양방향 이웃으로 읽는다.
-- 자기 비율만 보면 2청크 문서는 2/2 = 1.0으로 편향된다. 양쪽 비율을 요구하면
-- C 코퍼스 overlaps 정밀도가 0.17 → 0.41이고 판본 24/24는 유지된다. 대목 수 하한을
-- 양쪽 3으로 올리면 전체 기준 시뮬레이션에서 C 정밀도 0.857 → 0.889, 24/24 유지.
--
-- 함수 정의에 SET을 둔다. OpenProxy 풀 백엔드에 남은 PL/pgSQL generic plan에는
-- SET LOCAL이 DISCARD PLANS 뒤에야 먹었다. enable_seqscan=off를 함수 정의에
-- 묶어 프로브 계획에 적용하고, 함수 종료 시 세 설정 모두 호출 전 값으로 복원한다.
-- 실측 트리거 비용은 10청크 4.6s → 0.2s, 159청크 40s → 2.7s였다.
-- ready 트리거는 청크 교체와 같은 트랜잭션에서 호출한다. 실패는 삼키지 않아
-- 청크·관계가 함께 롤백되며, 일반 함수는 대량 적재 뒤 전량 재계산에도 재사용한다.

CREATE FUNCTION rebuild_document_edges(target_document_id uuid) RETURNS void
  LANGUAGE plpgsql
  SET hnsw.ef_search = 200
  SET random_page_cost = 1.1
  SET enable_seqscan = off
AS $$
DECLARE
  source_chunk record;
  nearest_chunks_json jsonb := '[]'::jsonb;
BEGIN
  DELETE FROM document_edges WHERE src_document_id = target_document_id;

  -- §14에서 외부 행 벡터를 참조하는 상관 LATERAL은 HNSW를 전혀 타지 않았다.
  -- 청크를 PL/pgSQL에서 하나씩 꺼내 각 SELECT에 상수 파라미터로 넘기면 벡터 정렬이
  -- HNSW 인덱스 스캔이 될 수 있다. 결과는 함수 메모리의 JSONB에만 모으며 OpenProxy로
  -- 누수되는 임시 테이블이나 영속 중간 테이블을 만들지 않는다 (ADR-022).
  FOR source_chunk IN
    SELECT chunk_index, embedding
    FROM document_chunks
    WHERE document_id = target_document_id
    ORDER BY chunk_index
  LOOP
    nearest_chunks_json := nearest_chunks_json || COALESCE(
      (
        SELECT jsonb_agg(
                 jsonb_build_object(
                   'src_chunk_index', source_chunk.chunk_index,
                   'dst_document_id', neighbor.document_id,
                   'dst_chunk_index', neighbor.chunk_index,
                   'dist', neighbor.dist
                 )
               )
        FROM (
          SELECT candidate.document_id,
                 candidate.chunk_index,
                 candidate.embedding <=> source_chunk.embedding AS dist
          FROM document_chunks candidate
          WHERE candidate.document_id <> target_document_id
          ORDER BY candidate.embedding <=> source_chunk.embedding
          LIMIT 10
        ) neighbor
      ),
      '[]'::jsonb
    );
  END LOOP;

  WITH
  -- NEIGHBOR_N = 10: 절대 거리 임계 없이, 10 < ef_search(200)을 유지한다.
  nearest_chunks AS (
    SELECT src_chunk_index, dst_document_id, dst_chunk_index, dist
    FROM jsonb_to_recordset(nearest_chunks_json) AS neighbor(
      src_chunk_index int,
      dst_document_id uuid,
      dst_chunk_index int,
      dist double precision
    )
  ),
  source_size AS (
    SELECT count(*) AS src_chunks
    FROM document_chunks
    WHERE document_id = target_document_id
  ),
  pair_stats AS (
    SELECT dst_document_id,
           count(DISTINCT src_chunk_index) AS matched_src,
           count(DISTINCT dst_chunk_index) AS matched_dst,
           min(dist) AS min_dist
    FROM nearest_chunks
    GROUP BY dst_document_id
  ),
  ranked_pairs AS (
    SELECT *,
           row_number() OVER (
             ORDER BY matched_src DESC, min_dist ASC, dst_document_id
           ) AS neighbor_rank
    FROM pair_stats
  ),
  capped_pairs AS (
    -- MAX_NEIGHBOR_DOCUMENTS = 5: kind 판정 전에 문서쌍 단위로 자른다.
    SELECT * FROM ranked_pairs WHERE neighbor_rank <= 5
  ),
  closest_chunks AS (
    SELECT DISTINCT ON (dst_document_id)
           dst_document_id, src_chunk_index, dst_chunk_index
    FROM nearest_chunks
    ORDER BY dst_document_id, dist, src_chunk_index, dst_chunk_index
  ),
  document_pairs AS (
    SELECT stats.dst_document_id,
           stats.matched_src::real / source_size.src_chunks AS overlap_ratio,
           closest.src_chunk_index, closest.dst_chunk_index, stats.min_dist,
           -- OVERLAP_RATIO = 0.8, MIN_MATCHED = 3: 양쪽 비율과 양쪽 세 대목 하한.
           -- 하한이 한쪽(2)에만 있으면 긴 문서가 계산 주체일 때 1청크 이웃이 1/1로 통과하고,
           -- 2청크에서는 비율이 0·0.5·1.0뿐이라 판별력이 없다 — 시연 코퍼스 실측 overlaps 44건.
           (stats.matched_src::real / source_size.src_chunks >= 0.8
            AND stats.matched_dst::real / target_size.dst_chunks >= 0.8
            AND stats.matched_src >= 3
            AND stats.matched_dst >= 3) AS is_overlaps
    FROM capped_pairs stats
    JOIN closest_chunks closest USING (dst_document_id)
    CROSS JOIN source_size
    CROSS JOIN LATERAL (
      -- UNIQUE (document_id, chunk_index) 인덱스로 대상 청크 수를 센다.
      SELECT count(*) AS dst_chunks
      FROM document_chunks
      WHERE document_id = stats.dst_document_id
    ) target_size
  )
  INSERT INTO document_edges
      (src_document_id, dst_document_id, kind,
       src_chunk_index, dst_chunk_index, score)
  SELECT target_document_id, dst_document_id,
         CASE WHEN is_overlaps THEN 'overlaps' ELSE 'related' END,
         CASE WHEN is_overlaps THEN NULL ELSE src_chunk_index END,
         CASE WHEN is_overlaps THEN NULL ELSE dst_chunk_index END,
         -- overlaps는 자기 매칭 비율, related는 최근접 청크 쌍 유사도다.
         CASE WHEN is_overlaps THEN overlap_ratio ELSE 1.0 - min_dist END
  FROM document_pairs;
END; $$;

-- 기존 트리거는 같은 함수 이름을 가리킨다. 설정은 판정 본체에만 둔다.
CREATE OR REPLACE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM rebuild_document_edges(NEW.id);
  RETURN NEW;
END; $$;
