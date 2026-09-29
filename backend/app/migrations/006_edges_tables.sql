-- 006_edges_tables.sql — 문서를 노드로 삼는 저장 관계 그래프 (ADR-029)
--
-- 노드는 항상 문서다. 위치가 의미 있는 points_to 계열 관계만 chunk_index를 채운다.
-- document_chunks.id는 bigserial이고 워커가 재임베딩 때 청크를 전량 교체하므로 id가
-- 매번 바뀐다(worker.py finalize_job). 관계는 문서 FK와 재계산 가능한 chunk_index를
-- 사용해야 재임베딩 뒤 다시 만들 수 있다.
--
-- dst_document_id가 NOT NULL인 이유: 대상이 제목 문자열인 위키링크는 이 테이블에
-- 들어오지 않으며 저장 위치는 m9에서 정한다. 미해결 링크를 미리 수용하는 nullable
-- 분기를 만들지 않는다.

CREATE TABLE document_edges (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  dst_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind            text NOT NULL,
  src_chunk_index int,
  dst_chunk_index int,
  score           real NOT NULL,

  -- 자기 edge는 순회가 같은 문서를 반복하게 하므로 저장 시점에 차단한다.
  CONSTRAINT document_edges_not_self CHECK (src_document_id <> dst_document_id),
  CONSTRAINT document_edges_kind_valid
    CHECK (kind IN ('overlaps', 'points_to', 'broader', 'related'))
);

-- broader는 src가 dst보다 포괄적이라는 방향이다. 반대 방향은 문서 id를 뒤집은 별도
-- 행으로 표현한다. related는 broader 방향 판정에 실패했을 때 관계 자체를 보존하는
-- 폴백이다.
--
-- score의 척도는 kind마다 다르다: overlaps는 매칭 비율, points_to는 유사도,
-- broader는 word_similarity의 방향 차다. 한 컬럼에 서로 다른 척도가 들어가므로
-- kind를 섞어 score로 정렬하면 안 된다.

-- PostgreSQL의 일반 UNIQUE는 NULL을 서로 다른 값으로 취급한다. 위치 없는 문서 관계도
-- 정확히 한 번만 저장되도록 NULL chunk_index를 -1로 접어 같은 관계 키로 만든다.
CREATE UNIQUE INDEX uq_document_edges_relation
  ON document_edges (
    src_document_id,
    dst_document_id,
    kind,
    COALESCE(src_chunk_index, -1),
    COALESCE(dst_chunk_index, -1)
  );
