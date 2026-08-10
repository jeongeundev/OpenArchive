-- 008_edges_triggers.sql — 임베딩 완료를 저장 관계 그래프로 확장한다 (ADR-029)
--
-- 이 함수는 worker.finalize_job의 DELETE → INSERT → status='ready' 트랜잭션 안에서
-- 실행된다. 따라서 함수가 시작될 때 NEW 문서의 청크는 전부 확정되어 있고 문서 행은
-- FOR UPDATE로 잠겨 있다. 관계 판정이 실패하면 예외를 삼키지 않으며, 같은 트랜잭션의
-- 청크 교체와 임베딩 완료도 함께 롤백되어 워커의 재시도·백오프가 처리한다.
--
-- §14 실측에서 60문서·262청크 전체는 176.1ms, 중앙값에 가까운 4청크 문서는
-- 2.92ms였다. 다만 당시 상관 LATERAL은 HNSW를 타지 않았으므로 규모 확장 근거가
-- 아니다. 실제 트리거 비용이 임베딩 완료를 유의미하게 늦추면 ADR-029의 edge_jobs
-- 재검토 조건에 해당한다.

CREATE FUNCTION build_document_edges() RETURNS trigger
  LANGUAGE plpgsql
  -- 함수 정의의 SET은 함수가 끝나면 이전 값으로 복원된다. finalize_job 트랜잭션의
  -- 나머지에 남는 SET LOCAL과 달리 관계 프로브 밖의 쿼리 계획을 오염시키지 않는다.
  SET hnsw.ef_search = 200
  SET random_page_cost = 1.1
AS $$
DECLARE
  source_chunk record;
  nearest_chunks_json jsonb := '[]'::jsonb;
BEGIN
  -- 재임베딩은 기존 청크를 전량 교체한다. NEW가 어느 쪽에 있든 과거 계산을 전부
  -- 지워야 낡은 위치와 점수가 남지 않는다. FK CASCADE만으로는 재임베딩을 못 잡는다.
  DELETE FROM document_edges
   WHERE src_document_id = NEW.id OR dst_document_id = NEW.id;

  -- §14에서 외부 행 벡터를 참조하는 상관 LATERAL은 HNSW를 전혀 타지 않았다.
  -- 청크를 PL/pgSQL에서 하나씩 꺼내 각 SELECT에 상수 파라미터로 넘기면 벡터 정렬이
  -- HNSW 인덱스 스캔이 될 수 있다. 결과는 함수 메모리의 JSONB에만 모으며 OpenProxy로
  -- 누수되는 임시 테이블이나 영속 중간 테이블을 만들지 않는다 (ADR-022).
  FOR source_chunk IN
    SELECT chunk_index, embedding
    FROM document_chunks
    WHERE document_id = NEW.id
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
          WHERE candidate.document_id <> NEW.id
          ORDER BY candidate.embedding <=> source_chunk.embedding
          LIMIT 10
        ) neighbor
      ),
      '[]'::jsonb
    );
  END LOOP;

  WITH
  -- NEIGHBOR_N = 10 (OPENSQL_RESEARCH §14). 절대 거리 임계 대신 순위 컷오프를 쓴다.
  -- 10 < ef_search(200)이라 ADR-011의 등호 과소검색 벽에서도 충분히 멀다.
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
    SELECT count(*)::real AS chunk_count
    FROM document_chunks
    WHERE document_id = NEW.id
  ),
  pair_stats AS (
    SELECT dst_document_id,
           count(DISTINCT src_chunk_index) AS matched_chunks,
           count(DISTINCT src_chunk_index)::real / source_size.chunk_count AS overlap_ratio,
           min(dist) AS min_dist
    FROM nearest_chunks
    CROSS JOIN source_size
    GROUP BY dst_document_id, source_size.chunk_count
  ),
  closest_chunks AS (
    SELECT DISTINCT ON (dst_document_id)
           dst_document_id, src_chunk_index, dst_chunk_index
    FROM nearest_chunks
    ORDER BY dst_document_id, dist, src_chunk_index, dst_chunk_index
  ),
  document_pairs AS (
    SELECT stats.dst_document_id, stats.overlap_ratio,
           closest.src_chunk_index, closest.dst_chunk_index, stats.min_dist,
           -- OVERLAP_RATIO = 0.8 (OPENSQL_RESEARCH §14)에 겹친 청크의 절대 수 하한을
           -- 더한다. 비율의 분모가 자기 청크 수여서, 청크가 하나뿐인 문서는 이웃에
           -- 걸리기만 해도 1/1 = 1.0이 되어 내용과 무관하게 "전반적으로 같은 내용"으로
           -- 단정된다. 실측: 청크 1개 문서 19개가 각각 평균 9.6건의 overlaps를 만들었다.
           -- 하한 2는 "두 대목 이상에서 만난다"를 요구해 그 자리를 막는다.
           (stats.overlap_ratio >= 0.8 AND stats.matched_chunks >= 2) AS is_overlaps
    FROM pair_stats stats
    JOIN closest_chunks closest USING (dst_document_id)
  ),
  classified AS (
    SELECT NEW.id AS src_document_id,
           dst_document_id,
           -- points_to 초기안과 BROADER_MARGIN=0.10 방향 판정은 실측에서 기각되어
           -- related로 보존한다.
           CASE WHEN is_overlaps THEN 'overlaps' ELSE 'related' END AS kind,
           CASE WHEN is_overlaps THEN NULL ELSE src_chunk_index END AS src_chunk_index,
           CASE WHEN is_overlaps THEN NULL ELSE dst_chunk_index END AS dst_chunk_index,
           CASE WHEN is_overlaps THEN overlap_ratio ELSE 1.0 - min_dist END AS score
    FROM document_pairs
  ),
  both_directions AS (
    SELECT src_document_id, dst_document_id, kind,
           src_chunk_index, dst_chunk_index, score
    FROM classified
    UNION ALL
    SELECT dst_document_id, src_document_id, kind,
           dst_chunk_index, src_chunk_index, score
    FROM classified
  )
  INSERT INTO document_edges
      (src_document_id, dst_document_id, kind,
       src_chunk_index, dst_chunk_index, score)
  SELECT src_document_id, dst_document_id, kind,
         src_chunk_index, dst_chunk_index, score
  FROM both_directions;

  -- kNN은 대칭이 아니지만 새 문서 기준 조회 한 번으로 양방향 행을 저장한다. 정확한
  -- 역방향은 기존 문서 전부를 다시 계산해 비용이 문서 수에 비례하므로 택하지 않았다.
  -- score의 척도도 kind마다 다르다: overlaps는 매칭 비율, related는 1-최소 거리다.
  -- 따라서 서로 다른 kind를 한 점수 축으로 섞어 정렬하면 안 된다.
  RETURN NEW;
END; $$;

-- ADR-029 §결정 2에서 실측 실패한 points_to·broader를 제거했다. 이전 마이그레이션은
-- 관계 후보를 열어 두었지만, 트리거가 실제 판정을 시작하는 이 시점부터 DB 불변식도
-- 저장 kind 두 종류와 맞춘다.
ALTER TABLE document_edges DROP CONSTRAINT document_edges_kind_valid;
ALTER TABLE document_edges ADD CONSTRAINT document_edges_kind_valid
  CHECK (kind IN ('overlaps', 'related'));

-- 세 조건이 모두 비용과 정합성의 일부다.
--
--   AFTER                         — finalize_job이 청크를 전부 INSERT한 뒤에 계산한다.
--   UPDATE OF embedding_status    — 제목·태그 변경은 관계 입력이 아니므로 재계산하지 않는다.
--   ready 전이 WHEN               — pending/processing → ready에서만 한 번 계산하고,
--                                   ready → ready 재진입으로 같은 비용을 반복하지 않는다.
CREATE TRIGGER trg_build_document_edges
  AFTER UPDATE OF embedding_status ON documents
  FOR EACH ROW
  WHEN (NEW.embedding_status = 'ready' AND OLD.embedding_status IS DISTINCT FROM 'ready')
  EXECUTE FUNCTION build_document_edges();
