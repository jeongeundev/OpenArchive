-- 036_preview_tables.sql — 원본 판당 파생 PDF 하나를 보관한다 (ADR-058 개정 결정 3).
-- 원본과 같은 DB에 두어 앱 호스트가 여럿이어도 한 곳에서 읽고 백업 하나로 보관한다
-- (ADR-046). 변환본은 다시 만들 수 있는 파생물이라 편집·버전·감사 대상이 아니며,
-- 원본 판이 지워지면 함께 지워진다. 열람·휴지통 조건은 미리보기 서비스가 확인한다.
-- 018의 원본 등록은 공급 자체라 트리거를 두지 않지만, 변환본은 파생물이다.
-- 변환본 행과 잡을 만드는 트리거는 다음 037_preview_triggers.sql이 담당한다.

CREATE TABLE document_file_previews (
  document_id uuid NOT NULL,
  file_version int NOT NULL,
  status text NOT NULL,
  pdf bytea,
  error text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, file_version),
  FOREIGN KEY (document_id, file_version)
    REFERENCES document_files (document_id, file_version) ON DELETE CASCADE,
  CONSTRAINT document_file_previews_status_valid
    CHECK (status IN ('pending', 'ready', 'failed', 'unavailable')),
  CONSTRAINT document_file_previews_ready_pdf
    CHECK ((status = 'ready') = (pdf IS NOT NULL)),
  CONSTRAINT document_file_previews_pdf_not_empty
    CHECK (pdf IS NULL OR octet_length(pdf) > 0)
);

-- 기존 (문서, 종류)당 pending 1건 코얼레싱은 그대로 쓴다 (016, 021).
ALTER TABLE embedding_jobs DROP CONSTRAINT embedding_jobs_kind_valid;
ALTER TABLE embedding_jobs ADD CONSTRAINT embedding_jobs_kind_valid
  CHECK (kind IN ('embed', 'edges', 'extract', 'preview'));
