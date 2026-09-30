-- 021_extract_tables.sql — 추출 상태와 「추출 중」 문서 행 (ADR-052 결정 4·5, #135)
--
-- 스캔 문서의 OCR은 쪽당 수 초라 업로드 요청 안에서 끝낼 수 없다. 추출은 워커 잡이 하고,
-- 문서 행은 빈 문서 텍스트로 먼저 생긴다. 이를 위해 세 불변식을 없애지 않고
-- **추출이 끝난 문서에 한정**한다 — 완료 표시와 빈 텍스트의 조합은 여전히 DB가 막는다.
--
-- 추출 상태를 embedding_status에 끼우지 않는 이유: 관계 트리거(ready 전이)·오류 문서
-- 화면·/admin/status가 그 값을 읽고, 인식 실패와 임베딩 실패는 사용자가 할 일이 다르다.
--
-- 002·016·018은 적용된 마이그레이션이라 고치지 않는다 — 러너가 파일명으로 이력을 남기므로
-- 고쳐도 기존 DB에 반영되지 않는다. 트리거 쪽 변경은 022_extract_triggers.sql.

-- pending = 추출 잡 대기·처리 중, failed = 인식 실패(재시도 없음), done = 문서 텍스트 확정.
-- 기본값 done: 기존 문서와 텍스트가 있는 업로드·텍스트 공급은 모두 추출이 끝난 문서다.
ALTER TABLE documents ADD COLUMN extraction_status text NOT NULL DEFAULT 'done';
ALTER TABLE documents ADD CONSTRAINT documents_extraction_status_valid
  CHECK (extraction_status IN ('pending', 'failed', 'done'));

-- 빈 본문 제약을 추출이 끝난 문서로 좁힌다. 이름은 그대로 둔다 — 위반 메시지를 읽는
-- 쪽이 제약 이름으로 판정한다. 제거 문자를 명시하는 이유는 002 주석 그대로다: btrim의
-- 1인자 형태는 공백만 제거해 탭·개행만 남은 추출 결과가 통과한다.
-- pending 문서에 "비어 있어야 한다"를 걸지 않는다 — 재추출 중인 기존 문서는 이전 텍스트를
-- 유지한 채 pending이 된다(ADR-052 결정 6).
ALTER TABLE documents DROP CONSTRAINT documents_content_not_blank;
ALTER TABLE documents ADD CONSTRAINT documents_content_not_blank
  CHECK (extraction_status <> 'done' OR length(btrim(content, E' \t\r\n\f')) > 0);

-- NULL = 이 판의 텍스트가 아직 추출되지 않았다. 「추출 중」 문서에는 텍스트 버전이 없어
-- 가리킬 대상이 없다. 워커가 v1을 쓰는 트랜잭션에서 채운다. 값이 있으면 FK가 여전히
-- 그 버전의 실재를 보장한다(복합 FK는 한 칸이라도 NULL이면 검사하지 않는다).
ALTER TABLE document_files ALTER COLUMN text_version DROP NOT NULL;

-- 잡 종류에 추출을 더한다. 코얼레싱 인덱스(uq_pending_job_per_doc_kind)는 016 그대로 —
-- 추출 잡도 (문서, 종류)당 pending 1건이고, 같은 문서의 임베딩 잡과 동시에 대기할 수 있다.
ALTER TABLE embedding_jobs DROP CONSTRAINT embedding_jobs_kind_valid;
ALTER TABLE embedding_jobs ADD CONSTRAINT embedding_jobs_kind_valid
  CHECK (kind IN ('embed', 'edges', 'extract'));
