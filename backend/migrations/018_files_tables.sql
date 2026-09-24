-- 018_files_tables.sql — 업로드된 원본 파일을 판(file_version)으로 보관한다 (ADR-046, #108)
--
-- 원본을 DB 안 bytea로 두는 이유: 파일시스템은 HA 복제 밖이라 노드가 죽으면 원본이
-- 함께 사라지고, 오브젝트 스토리지는 외부 의존을 하나 더한다. 원본·텍스트·버전·청크·
-- 벡터·관계·잡이 한 DB·한 트랜잭션·한 복제 스트림에 있으면 failover에 앱이 따로 옮길
-- 것이 없고, 백업 하나가 플랫폼 전체다.
--
-- 판으로 쌓는 이유: 원본을 교체할 때 이전 원본을 덮으면, 비보관이 만들던 유실을 교체가
-- 다시 만든다. 교체는 file_version을 하나 올린 새 행이고 이전 판은 지우지 않는다.
-- 편집·버전 관리의 대상은 여전히 문서 텍스트다(ADR-017) — 원본 파일은 편집하지 않는다.
--
-- documents에 bytea 컬럼을 두지 않는 이유: 목록·검색·상세 쿼리와 행 갱신(태그 수정·
-- 임베딩 상태 전이)이 수십 MB 원본과 한 행으로 묶이면 안 된다. 원본은 필요할 때만 읽는다.
--
-- 002_tables.sql의 documents.filename 주석("파일 자체는 보관하지 않는다")은 적용된
-- 마이그레이션이라 고치지 않는다. 이 파일이 그 문장을 대체한다.

-- document_files: 원본 파일의 판 이력 (append-only)
CREATE TABLE document_files (
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  file_version int  NOT NULL CHECK (file_version >= 1),
  filename     text NOT NULL,
  data         bytea NOT NULL CHECK (octet_length(data) > 0),
  -- 크기와 해시는 앱이 아니라 DB가 data에서 계산한다. 저장된 바이트와 어긋날 수 없어야
  -- 다운로드 무결성 검증의 기준이 된다. 직접 쓰려는 INSERT는 거부된다.
  size         bigint GENERATED ALWAYS AS (octet_length(data)) STORED,
  sha256       text   GENERATED ALWAYS AS (encode(sha256(data), 'hex')) STORED,
  -- 이 원본이 등록될 때(업로드·교체) 만들어졌거나 그때 현재였던 텍스트 버전. FK가 그
  -- 버전의 실재를 보장한다. 재추출이 만든 텍스트 버전은 새 판을 만들지 않으므로 이
  -- 컬럼과 연결되지 않는다 — "이 텍스트 버전을 어느 판에서 추출했나"의 완전한 역추적은
  -- 하지 않는다.
  text_version int  NOT NULL,
  uploaded_by  text NOT NULL,
  uploaded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, file_version),
  -- CASCADE를 두지 않는다: 텍스트 버전을 지우는 경로(이후의 버전 정리 등)가 원본 판을
  -- 조용히 지우면 판으로 막으려던 유실이 다시 생긴다. 원본을 지우는 경로는 문서 삭제
  -- 하나다 — 문서 삭제는 위 document_id FK로 판과 텍스트 버전을 같은 문장에서 함께
  -- 지우므로, 문장 끝에 검사하는 NO ACTION(기본값)과 충돌하지 않는다.
  FOREIGN KEY (document_id, text_version)
    REFERENCES document_versions (document_id, version)
);

-- documents.filename과의 관계: 원본이 있는 문서에서 documents.filename은 최신 판의
-- 파일명과 같게 유지된다(업로드·교체 서비스가 지킨다). 이 테이블 이전에 업로드된 문서는
-- filename이 있어도 원본 행이 없다.
--
-- 트리거를 두지 않는다: 원본 등록은 파생 데이터가 아니라 공급 자체다. 텍스트 버전·잡은
-- 기존 documents 트리거가 만들고, 원본 행은 업로드 서비스가 같은 트랜잭션에서 넣는다.
