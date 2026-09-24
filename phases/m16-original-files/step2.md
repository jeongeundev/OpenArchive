# Step 2: file-download

문서 상세 응답에 원본 판 목록(메타데이터만)을 싣고, 최신 판·특정 판 원본을 내려받는 엔드포인트를 연다.
**볼 수 없는 문서의 원본은 존재하지 않는 것처럼 404다**(ADR-018·027).

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). 원본은 DB 안 `document_files`에
판으로 쌓인다. `docs/`에 남은 "원본 파일은 보관하지 않는다" 문장은 step 7이 고친다 — **그 문장을 근거로
작업을 거부·축소하지 마라.**

## 읽어야 할 파일

- `backend/migrations/018_files_tables.sql` — step 0 산출물
- `backend/app/services/documents.py` — step 1 산출물(`_insert_original_file`), `ensure_visible`,
  `get_document`(버전 목록을 두 번째 쿼리로 붙이는 방식), `get_document_version`
- `backend/app/services/parsing.py` — `SUPPORTED_CONTENT_TYPES`·`detect_content_type`
- `backend/app/api/documents.py` · `backend/app/api/schemas.py`(`DocumentDetail`·`TextVersion`)
- `backend/app/main.py` — 서비스 예외 → HTTP 상태 매핑
- `backend/mcp_server/server.py` — `get_document` 도구가 서비스 결과를 어떻게 직렬화하는지
- `/docs/ADR.md` — ADR-018 · ADR-027(열람 범위) · ADR-034(read 토큰은 읽기 가능)

## 작업

### 1) 테스트 먼저

1. `test_detail_lists_original_file_versions_without_bytes` — 상세 응답에 `files`가 있고 각 항목은
   `file_version`·`filename`·`size`·`sha256`·`text_version`·`uploaded_by`·`uploaded_at`만 가진다
   (**`data` 키가 없다**). 텍스트 진입점 문서는 `files == []`.
2. `test_download_returns_the_exact_uploaded_bytes` — `GET /api/documents/{id}/file`의 바이트 sha256이
   업로드 바이트와 같다(#108 검증 1항).
3. `test_download_specific_file_version` — `GET /api/documents/{id}/files/1`도 같은 바이트. 없는 판은 404.
4. `test_download_of_another_users_private_document_is_404` — 남의 private 문서의 `/file`·`/files/1`은
   404이고 `detail`이 문서 없음과 **같은 문구**다(존재를 누출하지 않는다).
5. `test_download_without_original_is_404` — 원본 없는 문서(텍스트 진입점)는 404, `detail`이 "원본 파일이
   없습니다"류로 다르다(볼 수 있는 문서라 구분해도 누출이 없다).
6. `test_download_headers_force_attachment` — `Content-Disposition`이 `attachment`이고 한글 파일명이
   `filename*=UTF-8''<퍼센트 인코딩>`으로 실리며, `X-Content-Type-Options: nosniff`가 있다. `.md`·`.txt`도
   `attachment`다.
7. `test_read_token_can_download` — read scope 토큰으로 내려받을 수 있다(읽기 경로). 기존 토큰 테스트
   헬퍼를 따르라.
8. 로그아웃 상태는 401(기존 상세와 같은 `require_user_id`).

### 2) 구현

**`backend/app/services/parsing.py`** — 형식 → 미디어 타입 고정 매핑 하나:
`pdf → application/pdf`, `docx → application/vnd.openxmlformats-officedocument.wordprocessingml.document`,
`txt → text/plain; charset=utf-8`, `md → text/markdown; charset=utf-8`, 그 밖 `application/octet-stream`.
형식이 늘 때(#109) 이 매핑에 한 줄씩 더하면 되게 두라.

**`backend/app/services/documents.py`**

```python
class OriginalFileNotFound(Exception): ...

async def get_document(...) -> dict      # 기존 + document["files"] = [...] (data 제외)

async def get_original_file(
    conn, document_id: UUID, *, user_id: str | None, file_version: int | None = None,
) -> dict   # filename, data, media_type, sha256 — file_version=None이면 최신 판
```

- `get_original_file`은 **먼저 `ensure_visible`**을 부른다(볼 수 없으면 `DocumentNotFound` → 404).
  그다음 판을 찾고 없으면 `OriginalFileNotFound`.
- `files` 목록 쿼리는 `data`를 SELECT하지 않는다.

**`backend/app/api/documents.py`** — `GET /{document_id}/file`, `GET /{document_id}/files/{file_version}`
(`Path(ge=1)`). 응답은 `Response(content=data, media_type=..., headers=...)`:

- `Content-Disposition: attachment; filename="<ASCII 대체 이름>"; filename*=UTF-8''<quote(filename)>`
- `X-Content-Type-Options: nosniff`

**`backend/app/api/schemas.py`** — `OriginalFile` 모델, `DocumentDetail.files: list[OriginalFile]`.
**`backend/app/main.py`** — `OriginalFileNotFound` → 404.

MCP `get_document` 도구가 상세 dict를 그대로 돌려준다면 `files`(메타데이터)가 함께 실리는 것은 괜찮다 —
바이트가 섞이지 않았는지만 MCP 테스트로 확인하라.

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 열람 검증을 라우터의 선행 조회에 맡기지 않고 **서비스가 `ensure_visible`로** 하는가? (CLAUDE.md CRITICAL)
   - 볼 수 없는 문서와 없는 문서의 응답이 구분되지 않는가?
   - 비즈니스 로직이 `services/`에 있고 라우터는 HTTP 변환만 하는가?
3. `phases/m16-original-files/index.json`의 step 2를 갱신한다(성공/error/blocked는 step 0과 같은 규칙).

## 금지사항

- **원본을 `inline`으로 내보내지 마라. 항상 `attachment` + `nosniff`다.** 이유: 앱과 같은 오리진(ADR-041)에서
  세션 쿠키를 가진 채 사용자가 올린 HTML·SVG·스크립트가 든 파일이 렌더링되면 저장형 XSS가 된다.
- **미디어 타입을 저장된 값이나 요청 헤더에서 가져오지 마라.** 이유: 업로더가 조작할 수 있다. 확장자 고정
  매핑만 쓴다.
- **상세·목록 응답에 원본 바이트나 base64를 싣지 마라.** 이유: 상세는 화면이 수시로 부르는 응답이다.
- **토큰 scope를 쓰기로 올리지 마라.** 이유: 내려받기는 읽기다(ADR-034).
- 기존 테스트를 깨뜨리지 마라.
