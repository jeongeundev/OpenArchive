-- 037_preview_triggers.sql — 원본 판의 파생 미리보기와 변환 잡을 만든다 (ADR-058 결정 1·2).
-- 018은 원본 등록이 공급 자체라 트리거를 두지 않는다. 여기서 만드는 변환본은 파생물이다.
-- 원본 판은 바뀌지 않으므로 판마다 한 번 변환한다. 앱은 embedding_jobs와
-- document_file_previews에 직접 INSERT하지 않는다 — 워커는 변환본의 상태·PDF만 UPDATE한다.
-- 잡 코얼레싱은 uq_pending_job_per_doc_kind가 맡고, NOTIFY는 폴링의 최적화다 (ADR-009).

CREATE FUNCTION preview_convertible(filename text) RETURNS boolean
  LANGUAGE sql IMMUTABLE
AS $$
  SELECT lower(substring(filename FROM '\.([^.]*)$'))
    IN ('hwp', 'hwpx', 'docx', 'xlsx', 'pptx');
$$;

CREATE FUNCTION on_document_file_preview_requested() RETURNS trigger
  LANGUAGE plpgsql
AS $$
BEGIN
  -- fail_job의 pending 유무 판정·복귀 사이에 새 잡이 끼지 않도록 문서부터 잠근다.
  PERFORM 1 FROM documents WHERE id = NEW.document_id FOR UPDATE;
  INSERT INTO document_file_previews (document_id, file_version, status)
    VALUES (NEW.document_id, NEW.file_version, 'pending') ON CONFLICT DO NOTHING;
  INSERT INTO embedding_jobs (document_id, kind)
    VALUES (NEW.document_id, 'preview') ON CONFLICT DO NOTHING;
  PERFORM pg_notify('embedding_jobs', '');
  RETURN NEW;
END; $$;

CREATE TRIGGER trg_document_files_preview_requested
  AFTER INSERT ON document_files
  FOR EACH ROW WHEN (preview_convertible(NEW.filename))
  EXECUTE FUNCTION on_document_file_preview_requested();

-- 설치 전 판과 실패·변환기 없음 판을 다시 건다. ready는 보존한다.
-- 돌려주는 값은 pending 판 수다. 새 잡 수가 아니다 — 코얼레싱된 잡도 이 요청의 일을 한다.
CREATE FUNCTION enqueue_all_preview_jobs() RETURNS bigint
  LANGUAGE plpgsql
AS $$
DECLARE
  target_count bigint;
BEGIN
  -- 트리거·fail_job과 같은 잠금 순서다. 문서 id 순으로 잠가 일괄 호출끼리의 교착을 피한다.
  PERFORM d.id FROM documents d
    WHERE EXISTS (
      SELECT 1 FROM document_files f
      WHERE f.document_id = d.id AND preview_convertible(f.filename)
    )
    ORDER BY d.id FOR UPDATE;
  INSERT INTO document_file_previews (document_id, file_version, status)
    SELECT document_id, file_version, 'pending' FROM document_files
    WHERE preview_convertible(filename) ON CONFLICT DO NOTHING;
  UPDATE document_file_previews p
    SET status = 'pending', pdf = NULL, error = NULL, updated_at = now()
    FROM document_files f
    WHERE (p.document_id, p.file_version) = (f.document_id, f.file_version)
      AND preview_convertible(f.filename) AND p.status IN ('failed', 'unavailable');
  INSERT INTO embedding_jobs (document_id, kind)
    SELECT DISTINCT p.document_id, 'preview' FROM document_file_previews p
    JOIN document_files f USING (document_id, file_version)
    WHERE preview_convertible(f.filename) AND p.status = 'pending'
    ON CONFLICT DO NOTHING;
  SELECT count(*) INTO target_count FROM document_file_previews p
    JOIN document_files f USING (document_id, file_version)
    WHERE preview_convertible(f.filename) AND p.status = 'pending';
  PERFORM pg_notify('embedding_jobs', '');
  RETURN target_count;
END; $$;
