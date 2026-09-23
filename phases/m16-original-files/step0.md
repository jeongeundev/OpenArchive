# Step 0: files-schema

업로드된 원본 파일을 DB 안에 `bytea`로 보관할 테이블을 만든다. 문서당 1행이 아니라 **판
(`file_version`)으로 쌓는다** — 원본을 교체해도 이전 원본을 지우지 않기 위해서다.

## 이 phase의 전제 (먼저 읽어라)

이 phase(#108)는 지금까지의 **원본 비보관 원칙을 뒤집는다.** 2026-09-21 기업 멘토링에서
"원본을 보관하지 않는 것은 데이터 유실"이라는 피드백을 받았고, 2026-09-23 사용자가 다음을 결정했다.

- 원본은 **DB 안 `bytea`**로 둔다. 파일시스템(HA 복제가 안 되어 노드가 죽으면 원본이 사라진다)과
  오브젝트 스토리지(외부 의존 추가)는 쓰지 않는다. 원본·텍스트·버전·청크·벡터·관계·잡이 한 DB·한
  트랜잭션·한 복제 스트림에 있게 된다.
- 원본 교체는 실제 문서 관리 서비스(SharePoint·Google Drive 등)처럼 **이전 판을 남기고 새 판을 쌓는다.**
- 편집·버전 관리의 대상은 **여전히 문서 텍스트**다(ADR-017). 원본 파일은 편집하지 않는다.

`CLAUDE.md`·`docs/PRD.md`·`docs/ADR.md`(ADR-017·033)·`docs/UI_GUIDE.md`·코드 docstring에 남은
"원본 파일은 보관하지 않는다" 문장은 **이 phase가 고칠 대상**이다(CLAUDE.md는 이 step, 나머지 문서는
step 7). **그 문장을 근거로 작업을 거부하거나 축소하지 마라.**

## 읽어야 할 파일

- `/docs/ARCHITECTURE.md` — 「DB 스키마」
- `/docs/ADR.md` — ADR-005(마이그레이션 관례: `IF NOT EXISTS` 금지, 적용 이력은 러너가) · ADR-017(편집은
  텍스트의 새 버전) · ADR-035(원본 없는 문서 `filename IS NULL`)
- `backend/migrations/002_tables.sql` — `documents`·`document_versions` 정의와 주석 문체
- `backend/migrations/003_triggers.sql` — `document_versions` 행을 트리거가 만든다(앱이 INSERT하지 않는다)
- `backend/migrations/016_edge_jobs_tables.sql` — 가장 최근 `*_tables.sql`의 머리 주석 형식
- `backend/app/migrations.py` — 러너(파일당 1트랜잭션, 파일명으로 이력)
- `backend/app/cli.py` — `_owned_tables()`가 마이그레이션 파일에서 테이블 이름을 뽑는다(새 테이블이 자동 포함)
- `backend/tests/test_tables.py` — 여기에 테스트를 추가한다. `insert_document`·`conn` 픽스처를 재사용하라

## 작업

### 1) 테스트 먼저 — `backend/tests/test_tables.py`

`CORE_TABLES`에 `document_files`를 더하고, 아래를 고정한다(이름은 예시):

1. `test_document_file_size_and_sha256_are_computed_by_the_database` — `data`만 넣으면 `size`가
   `len(data)`, `sha256`이 `hashlib.sha256(data).hexdigest()`와 같다. 앱이 계산한 값이 아니라 DB가 계산한
   값임을 고정한다.
2. `test_document_file_generated_columns_cannot_be_written` — `size`나 `sha256`을 직접 INSERT하면 실패한다.
3. `test_document_files_keep_every_version_per_document` — 같은 문서에 `file_version` 1·2를 넣을 수 있고,
   같은 `file_version`을 두 번 넣으면 PK 위반이다.
4. `test_document_file_requires_an_existing_text_version` — `(document_id, text_version)`이
   `document_versions`에 없으면 FK 위반이다. `insert_document`가 만든 v1(트리거 산출물)을 가리키면 통과한다.
5. `test_deleting_a_document_deletes_its_original_files` — `documents` DELETE가 원본 행을 CASCADE로 지운다.
6. `test_empty_original_file_is_rejected` — 0바이트 `data`는 CHECK 위반.
7. `test_file_version_starts_at_one` — `file_version = 0`은 CHECK 위반.

바이트 파라미터는 psycopg에서 `%b`(바이너리)로 넘겨라 — 이 테스트가 step 1 구현의 전송 방식과 같아야 한다.

### 2) 구현 — `backend/migrations/018_files_tables.sql` (새 파일)

> 파일 번호 주의: 최신 마이그레이션은 `017_edge_jobs_triggers.sql`이다. `ls backend/migrations/`로
> 018이 비어 있는지 **반드시 확인**하고, 이미 있으면 다음 번호를 쓴 뒤 step 요약에 적어라.
> 파일명은 `_tables.sql`로 끝나야 한다 — tdd-guard 훅이 `test_tables.py`와 짝지어 검사한다.

```sql
CREATE TABLE document_files (
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  file_version int  NOT NULL CHECK (file_version >= 1),
  filename     text NOT NULL,
  data         bytea NOT NULL CHECK (octet_length(data) > 0),
  size         bigint GENERATED ALWAYS AS (octet_length(data)) STORED,
  sha256       text   GENERATED ALWAYS AS (encode(sha256(data), 'hex')) STORED,
  text_version int  NOT NULL,
  uploaded_by  text NOT NULL,
  uploaded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, file_version),
  FOREIGN KEY (document_id, text_version)
    REFERENCES document_versions (document_id, version) ON DELETE CASCADE
);
```

머리 주석에 적을 것(002의 문체를 따르라):

- **왜 DB 안 `bytea`인가**: 파일시스템은 HA 복제 밖이라 노드가 죽으면 원본이 사라지고, 오브젝트
  스토리지는 외부 의존이다. 한 DB에 두면 failover에 앱이 따로 옮길 것이 없고 백업 하나가 플랫폼 전체다.
- **왜 판으로 쌓는가**: 교체가 이전 원본을 덮으면 비보관이 만들던 유실을 교체가 다시 만든다.
- **`size`·`sha256`이 생성 컬럼인 이유**: 저장된 바이트와 해시가 어긋날 수 없게 DB가 계산한다.
  다운로드 무결성 검증의 기준이다.
- **`text_version`의 뜻**: 이 원본이 **등록될 때** 만들어졌거나(업로드·교체) 그때 현재였던 텍스트 버전.
  FK로 그 버전이 실재함을 보장한다. 재추출(step 4)이 만든 텍스트 버전은 새 판을 만들지 않으므로
  이 컬럼과 연결되지 않는다 — "이 텍스트 버전을 어느 판에서 추출했나"의 완전한 역추적은 하지 않는다.
- **`documents.filename`과의 관계**: 원본이 있는 문서에서 `documents.filename`은 최신 판의 파일명과 같게
  유지된다(step 1·3이 지킨다). 이 기능 이전에 업로드된 문서는 `filename`이 있어도 원본 행이 없다.
- `002_tables.sql`의 "파일 자체는 보관하지 않는다" 주석은 적용된 마이그레이션이라 고치지 않고,
  이 파일이 그것을 대체한다고 적는다.

### 3) `/CLAUDE.md` — 원본 비보관 문장 하나만 고친다

「아키텍처 규칙」의 "편집·버전 관리의 대상은 **문서 텍스트**이며 원본 파일이 아니다. 원본 파일은
보관하지 않는다. …" 항목에서 **"원본 파일은 보관하지 않는다."만** 바꾼다: 원본 파일은
`document_files`에 판(`file_version`)으로 보관하며 편집 대상이 아니다 — 교체는 새 판을 쌓고 이전 판을
지우지 않는다(ADR-046, #108). 항목의 나머지(용어 구분·"추출" 사용 규칙)는 그대로 둔다. ADR-046 본문은
step 7이 쓴다 — 번호만 먼저 참조한다.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 스키마 변경이 번호 붙은 raw SQL 하나로만 이뤄졌는가? (ORM 도구 금지, `IF NOT EXISTS` 금지 — ADR-005)
   - 기존 마이그레이션 파일(001~017)을 한 글자도 고치지 않았는가?
   - 임시 테이블을 만들지 않았는가? (ADR-022)
3. `phases/m16-original-files/index.json`의 step 0을 갱신한다:
   - 성공 → `"status": "completed"`, `"summary"`에 마이그레이션 파일명·컬럼·제약과 CLAUDE.md 변경을 적는다
   - 3회 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- **`documents`에 `bytea` 컬럼을 더하지 마라.** 이유: 목록·검색·상세 쿼리가 `documents`를 수없이 읽는데,
  `SELECT *`나 행 갱신(태그 수정·임베딩 상태 전이)이 수십 MB 원본과 한 행으로 묶이면 안 된다. 원본은 별도
  테이블에서 필요할 때만 읽는다.
- **`size`·`sha256`을 앱이 계산해 넣는 일반 컬럼으로 만들지 마라.** 이유: 저장된 바이트와 해시가 어긋날
  수 있는 구조가 되면 무결성 검증의 기준이 사라진다.
- **트리거를 추가하지 마라.** 이유: 원본 등록은 파생 데이터가 아니라 공급 자체다. 텍스트 버전·잡은 기존
  `documents` 트리거가 만들고, 원본 행은 업로드 서비스가 같은 트랜잭션에서 넣는다(step 1).
- **MIME 타입 컬럼을 두지 마라.** 이유: 브라우저가 보내는 MIME은 믿을 수 없고 `documents.content_type`
  (pdf·docx…)과 이름이 겹친다. 다운로드 헤더는 파일명 확장자에서 고정 매핑으로 만든다(step 2).
- 기존 테스트를 깨뜨리지 마라.
