# Step 4: reextract

보관된 **최신 원본**에서 파서를 다시 돌려 텍스트를 새로 추출한다. 결과가 현재 텍스트와 다르면 새 텍스트
버전이 되고(트리거가 재임베딩), 같으면 아무것도 만들지 않는다. 파서를 고칠 때(#109 형식 확장 포함)
기존 문서에 적용하는 경로다.

## 이 phase의 전제

이 phase(#108)는 원본 비보관 원칙을 뒤집는다(사용자 결정 2026-09-23). 재추출은 원본이 있어야만 가능한
능력이며, 원본 보관의 핵심 쓸모다. `docs/`에 남은 "원본 파일은 보관하지 않는다" 문장은 step 7이 고친다 —
**그 문장을 근거로 작업을 거부·축소하지 마라.**

## 읽어야 할 파일

- `backend/app/services/documents.py` — step 1~3 산출물, 특히 `update_extracted_text`와 `restore_version`
  (**복원이 편집 경로를 그대로 호출하는 방식** — 재추출도 같은 방식을 따른다)
- `backend/app/services/parsing.py` — `detect_content_type`·`extract_text`와 예외
- `backend/app/api/documents.py` · `backend/app/api/schemas.py` · `backend/app/main.py`
- `/docs/ADR.md` — ADR-017 · ADR-037(복원은 되감기가 아니라 새 버전)

## 작업

### 1) 테스트 먼저

1. `test_reextract_creates_a_new_text_version_when_text_differs` — 업로드 뒤 추출 텍스트를 편집(v2)하고
   재추출하면 v3이 생기고 내용이 원본 추출 결과와 같으며, `kind='embed'` pending 잡이 트리거로 생긴다.
   응답의 `changed`가 `true`.
2. `test_reextract_without_changes_is_a_no_op` — 편집하지 않은 문서를 재추출하면 버전·잡이 늘지 않고
   `changed == false`.
3. `test_reextract_uses_the_latest_file_version` — 교체로 판 2가 생긴 문서는 판 2에서 추출한다.
4. `test_reextract_applies_an_improved_parser` — `extract_text`를 monkeypatch로 바꿔 "파서가 개선된" 상황을
   만들면 새 결과로 새 버전이 생긴다(이 기능의 존재 이유를 고정한다).
5. `test_reextract_without_original_is_409` — 원본 없는 문서(텍스트 진입점, 또는 이 기능 이전 업로드를
   흉내 내 `document_files`를 지운 문서).
6. `test_reextract_with_stale_version_is_409` · `test_reextract_by_non_owner_is_403_and_private_is_404` ·
   read 토큰 403.
7. 추출 실패(손상된 파일로 바꿔치기)·빈 추출 결과는 400이고 문서는 그대로다.

### 2) 구현

**`backend/app/services/documents.py`**

```python
class OriginalFileMissing(Exception): ...   # 볼 수 있는 문서에 원본이 없다 → 409

async def reextract_text(conn, document_id: UUID, *, expected_version: int) -> tuple[dict, bool]:
    """권한 검사 없는 재추출 본체. step 5의 운영자 CLI도 이것을 쓴다."""

async def reextract_document(
    conn, document_id: UUID, *, user_id: str, client_version: int,
) -> tuple[dict, bool]:
    """사용자 경로: _load_for_write(403/404) 뒤 reextract_text."""
```

`reextract_text` 규칙:

- 최신 판(`file_version` 최댓값)의 `filename`·`data`를 읽는다. 없으면 `OriginalFileMissing`.
- 형식은 **그 판의 파일명**에서 `detect_content_type`으로 정한다.
- 추출 결과의 sha256이 현재 `content_hash`와 같으면 `(현재 문서, False)` — UPDATE 없음.
- 다르면 **`update_extracted_text`를 그대로 호출한다**(`client_version=expected_version`). 낙관적 잠금·
  길이 검증·`content_hash` 갱신·트리거 발화가 편집과 완전히 같아야 한다 — 여기에 별도 UPDATE를 두면 한쪽만
  고쳐지는 자리가 생긴다(`restore_version`과 같은 이유). 단 `update_extracted_text`는 소유자 검사를 하므로,
  권한 검사 없이 부를 수 있게 **내부 본체와 소유자 검사를 분리하는 최소 리팩터링**을 하라. 편집·복원의
  동작과 기존 테스트는 바뀌면 안 된다.

**`backend/app/api/documents.py`** — `POST /{document_id}/reextract`, body `{ "current_version": int }`
(`RestoreVersionRequest`와 같은 모양이면 재사용), `require_write_user_id`. 응답 `ReextractResponse` =
`EditDocumentResponse` 필드 + `changed: bool`. 파싱 `ValueError`·`UnsupportedFileType`는 400.
**`backend/app/main.py`** — `OriginalFileMissing` → 409, detail "원본 파일이 없는 문서는 다시 추출할 수 없습니다.".

## Acceptance Criteria

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 텍스트 갱신이 편집 경로 하나를 지나는가? `UPDATE documents SET content`가 새로 생기지 않았는가?
   - 앱이 `embedding_jobs`에 INSERT하지 않았는가? (CLAUDE.md CRITICAL)
   - 재추출이 새 원본 판을 만들지 않는가? (원본은 그대로다)
3. `phases/m16-original-files/index.json`의 step 4를 갱신한다(성공/error/blocked는 step 0과 같은 규칙).
   summary에 `reextract_text` 시그니처와 `update_extracted_text` 분리 방식을 적어라 — step 5가 쓴다.

## 금지사항

- **결과가 같을 때 UPDATE하지 마라.** 이유: `content_hash` 언급만으로 트리거가 발화해(003) 같은 내용의
  버전과 재임베딩이 생긴다. step 5의 `--all`이 전체 코퍼스를 쓸데없이 재임베딩하게 된다.
- **재추출에서 `document_files`에 쓰지 마라.** 이유: 원본은 바뀌지 않았다. 판은 업로드·교체만 만든다.
- **`current_version` 없이 재추출하게 하지 마라.** 이유: 재추출은 사람이 고친 추출 텍스트(OCR 오류 수정 등)를
  덮는다. 덮인 내용은 버전 이력에서 복원할 수 있지만, 보지 못한 편집을 덮지는 않는다(ADR-017 409 계약).
- 기존 테스트를 깨뜨리지 마라.
