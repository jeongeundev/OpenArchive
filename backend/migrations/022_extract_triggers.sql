-- 022_extract_triggers.sql — 추출이 필요한 문서는 추출 잡을 남긴다 (ADR-052 결정 3·5·6, #135)
-- 스키마 변경은 021_extract_tables.sql.
--
-- 잡 생성은 여기서도 DB 계층이다 — 앱은 extraction_status를 'pending'으로 두기만 하고
-- embedding_jobs에 INSERT하지 않는다(ADR-001과 같은 원칙).

-- (1) 문서 텍스트 트리거는 추출이 끝난 문서에서만 발화한다.
--     추출 중 INSERT가 발화하면 빈 v1이 이력에 남고 청크가 나올 수 없는 임베딩 잡이 돈다.
--     v1은 워커가 첫 텍스트를 쓰는 UPDATE(content·content_hash·extraction_status='done'을
--     한 문장에서)가 기록한다. 함수 본체(003)와 나머지 조건은 그대로다.
DROP TRIGGER trg_documents_content_changed ON documents;
CREATE TRIGGER trg_documents_content_changed
  AFTER INSERT OR UPDATE OF content_hash ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0 AND NEW.extraction_status = 'done')
  EXECUTE FUNCTION on_document_content_changed();

-- (2) 추출 요청 — 추출 중으로 들어오거나(새 스캔 문서) 추출 중으로 바뀌면(OCR 대상의
--     재추출·원본 교체) 추출 잡을 남기고 워커를 깨운다. 텍스트 버전은 건드리지 않는다 —
--     재추출이 끝날 때까지 이전 텍스트·청크로 검색된다.
--     코얼레싱은 uq_pending_job_per_doc_kind가 한다. NOTIFY는 최적화이고 폴링이 주 경로다
--     (ADR-009). failed·done으로의 전이는 WHEN에서 걸러져 잡을 만들지 않는다.
CREATE FUNCTION on_document_extraction_requested() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO embedding_jobs (document_id, kind) VALUES (NEW.id, 'extract')
    ON CONFLICT DO NOTHING;
  PERFORM pg_notify('embedding_jobs', NEW.id::text);
  RETURN NEW;
END; $$;

CREATE TRIGGER trg_documents_extraction_requested
  AFTER INSERT OR UPDATE OF extraction_status ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0 AND NEW.extraction_status = 'pending')
  EXECUTE FUNCTION on_document_extraction_requested();
