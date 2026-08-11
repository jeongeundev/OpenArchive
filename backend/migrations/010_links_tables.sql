-- 010_links_tables.sql — 조회 시점에 해석하는 문서 위키링크 (ADR-027)
--
-- target_document_id를 저장하지 않는다. 링크 생성 트리거에는 사용자 컨텍스트가 없고,
-- 저장 시점에 대상을 굳히면 public·private 동명 문서 중 private가 선택되어 사용자가
-- 볼 수 있는 문서까지 가려질 수 있다. target_title만 저장하고 열람 범위 안에서 조회
-- 시점에 해석한다.
--
-- document_edges와는 대상 타입과 생성 시점이 다르다. edge는 벡터가 준비된 문서 id
-- 사이의 관계지만, 링크는 임베딩을 기다리지 않고 본문의 제목 문자열에서 즉시 생긴다.
-- 존재하지 않는 제목도 저장한다. 깨진 링크는 오류가 아니라 위키의 정상 기능이다.

CREATE TABLE document_links (
  src_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  src_chunk_index int,
  target_title    text NOT NULL
);

-- PostgreSQL의 일반 UNIQUE는 NULL을 서로 다른 값으로 취급한다. 아직 알 수 없는 청크
-- 위치를 -1로 접어 같은 문서·제목 링크가 본문에 여러 번 나와도 한 행만 남긴다.
CREATE UNIQUE INDEX uq_document_links_source_title_position
  ON document_links (
    src_document_id,
    target_title,
    COALESCE(src_chunk_index, -1)
  );
