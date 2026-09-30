-- 024_edges_triggers.sql — 재계산은 계산할 문서 행을 먼저 잠근다 (#95-e2 실측 교착)
--
-- 워커의 관계 잡은 문서를 잠근 뒤 이 함수를 불렀지만 `rebuild-edges`(demo가 부른다)는 잠그지
-- 않았다. 한 문서에서 둘이 겹치면 잠그지 않은 쪽이 옛 관계를 먼저 지우고, 잠근 쪽은 그 행에서
-- 기다리고, 잠그지 않은 쪽의 새 관계 INSERT는 FK 검사(`FOR KEY SHARE`)로 잠근 쪽을 기다려
-- 교착했다 — 새 DB에 BGE-M3로 demo를 돌려 4회 중 1회.
--
-- 호출하는 쪽마다 잠그게 하지 않고 함수 첫 문장에 둔다. 판정 본체가 이 함수 하나이듯, 같은
-- 문서의 재계산이 차례로 도는 것도 여기서 보장해야 새 호출 경로가 잊을 수 없다. 그래야 두 번째
-- 호출의 DELETE가 첫 호출이 커밋한 행을 보고 교체한다 — 잠금이 DELETE 뒤에 오면 UniqueViolation.
--
-- `FOR NO KEY UPDATE`인 이유: FK 검사와 함께 걸린다. `FOR UPDATE`면 서로의 이웃인 두 문서를
-- 동시에 계산할 때 각자 자기 문서를 쥔 채 상대를 가리키는 관계를 넣으려다 교착한다(실측).
-- 청크 교체(finalize_job의 `FOR UPDATE`)와 다른 재계산은 그대로 막는다.
--
-- 판정은 014와 같다. 러너가 파일명으로 적용 이력을 남기므로 014를 고치지 않고 새로 둔다.

CREATE OR REPLACE FUNCTION rebuild_document_edges(target_document_id uuid) RETURNS void
  LANGUAGE plpgsql
  SET hnsw.ef_search = 200
  SET random_page_cost = 1.1
  SET enable_seqscan = off
AS $$
DECLARE
  source_chunk record;
  nearest_chunks_json jsonb := '[]'::jsonb;
BEGIN
  PERFORM 1 FROM documents WHERE id = target_document_id FOR NO KEY UPDATE;
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
