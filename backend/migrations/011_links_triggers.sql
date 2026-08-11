-- 011_links_triggers.sql — 본문을 진실로 삼는 위키링크 교체 트리거
--
-- 벡터나 청크가 필요 없으므로 임베딩 완료를 기다리지 않는다. 기존 임베딩 트리거와
-- 함수를 공유하지 않아 링크 파싱과 아웃박스 생성의 실패 경계를 분리한다.

CREATE FUNCTION replace_document_links() RETURNS trigger AS $$
BEGIN
  DELETE FROM document_links WHERE src_document_id = NEW.id;

  INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
  SELECT DISTINCT NEW.id, NULL::int, btrim(match[1])
  FROM regexp_matches(NEW.content, '\[\[([^\[\]]+)\]\]', 'g') AS parsed(match)
  WHERE btrim(match[1]) <> ''
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_replace_document_links
  AFTER INSERT OR UPDATE OF content_hash ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0)
  EXECUTE FUNCTION replace_document_links();
