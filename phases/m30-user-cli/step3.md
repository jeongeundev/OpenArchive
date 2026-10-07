# Step 3: doc-write

쓰기 명령 `doc upload`·`doc edit`·`doc restore`·`doc tag`·`doc delete [--permanent]`·`doc trash list`·`doc trash restore`를 만든다.

## 공통 배경 — m30-user-cli 설계 결정 (모든 step 같음)

이슈 #189. 근거 **ADR-057**(사용자 CLI는 REST API의 클라이언트, 채택 2026-10-05)·**ADR-060 결정 8**(삭제는 휴지통)·**ADR-048**(503 재시도)·ADR-034(위임 토큰)·ADR-061 결정 1(토큰 만료일, #199에서 구현됨).

지금 `openarchive` CLI(`backend/openarchive/cli.py`, 1651줄)는 **운영자 도구**다 — `--dsn`(또는 `DATABASE_URL`)으로 DB에 직결하고 `--user`로 아무 사용자나 비밀번호 없이 지정한다. 이 phase는 같은 실행 파일에 **사용자 CLI**를 더한다: `gh`처럼 서버 REST API에 **본인 API 토큰(Bearer)** 으로만 붙는 클라이언트다. 열람 범위·소유자 검사·낙관적 잠금·read 토큰 거부·감사 로그는 서버가 웹과 똑같이 적용한다 — **CLI가 규칙을 다시 구현하지 않는다.**

사실 (탐색으로 확인):

- 필요한 REST 엔드포인트는 **이미 전부 있다**(`backend/openarchive/api/documents.py`·`search.py`·`ask.py`·`auth.py`):
  - 목록 `GET /api/documents?sort=updated&limit=1..100&offset=` → `list[DocumentSummary]`(`id,title,filename,content_type,version,tags,embedding_status,extraction_status,…`)
  - 상세 `GET /api/documents/{id}` → `DocumentDetail`(`content`, `versions`, `files[]`(`file_version,filename,size,sha256,…`))
  - 텍스트 버전 `GET /api/documents/{id}/versions/{n}` → `TextVersionDetail`(`content` 포함)
  - 업로드 `POST /api/documents` multipart(`file`, `title`, `tags`(반복 필드), …) + 헤더 `Idempotency-Key` → 201 `DocumentSummary`
  - 편집 `PUT /api/documents/{id}` JSON `{content, version}` → `EditDocumentResponse`(새 `version`)
  - 버전 되돌리기 `POST /api/documents/{id}/versions/{n}/restore` JSON `{current_version}` → `EditDocumentResponse`
  - 태그 `PUT /api/documents/{id}/tags` JSON `{tags}` → `DocumentSummary`
  - 원본 `GET /api/documents/{id}/file` → 최신 원본 바이트
  - 삭제 `DELETE /api/documents/{id}`(휴지통) · `DELETE /api/documents/{id}?permanent=true`(영구) → 204
  - 휴지통 `GET /api/documents/trash` → `list[TrashItem]`(`id,title,deleted_at,purge_at`, **write scope 필요**) · 복원 `POST /api/documents/{id}/restore`
  - 검색 `POST /api/search` JSON `{query,tags,content_type,k}` → `{items:[SearchResult], sql}` · 답변 `POST /api/ask` → `{status: answered|no_evidence|disabled|failed, answer, detail, sources, items}`
  - 주체 `GET /api/auth/me` → `AuthStatus` — **step 0에서 `scope`·`expires_at`을 더한다**
- 서버 오류 문구: 볼 수 없거나 없는 문서는 404 `"문서를 찾을 수 없습니다."`(존재를 드러내지 않음), read 토큰의 쓰기는 403 `"쓰기 권한이 필요합니다."`, 버전 충돌은 409 `{"detail": "...", "current_version": N}`(다른 409 — 인식 중 등 — 에는 `current_version` 키가 없다), DB 일시 불가용은 503 + `Retry-After`.
- 토큰 판정은 `services/auth.py validate_token` 하나다. 만료·폐기·틀린 값은 모두 익명 처리된다(`/api/auth/me`가 `authenticated: false`). 공유 토큰으로 `/me`를 부르면 403이다.
- 설정 디렉터리는 `openarchive.config.openarchive_home()`(`$OPENARCHIVE_HOME` 또는 `~/.openarchive`). 같은 디렉터리에 서버용 `.env`가 있을 수 있다.
- `httpx` 0.28은 이미 설치되어 있다(`mcp`가 끌고 옴, BSD-3). 지금은 `[dev]`에만 적혀 있다.

결정 (2026-10-08 사용자 승인):

- **D1** `httpx`를 `backend/pyproject.toml` 런타임 `dependencies`에 **명시**한다(주석에 라이선스와 이유 — 사용자 CLI가 직접 import).
- **D2** `/api/auth/me`의 `AuthStatus`에 `scope: str | None`, `expires_at: datetime | None`을 더한다. **토큰 주체일 때만** 값이 있고 세션·익명이면 둘 다 `null`. 기존 세 필드의 의미는 그대로다.
- **D3 재시도** — 웹 UI(`frontend/src/lib/api.ts fetchWithBackoff`)와 **같은 기준**: 읽기(GET), 메서드만 POST인 읽기(`/api/search`·`/api/ask`), `Idempotency-Key`가 붙은 업로드(`POST /api/documents`)만 503·연결 오류에 재시도한다. 지수 백오프 1초 시작·상한 8초·전체 지터(`random() * min(8, 1 * 2**attempt)`), 총 60초 예산, 503의 `Retry-After`(초 또는 HTTP 날짜)보다 일찍 보내지 않는다(`max(Retry-After, 지터)`). 재시도 중이면 stderr에 한 번 「서버가 일시적으로 응답하지 않아 다시 시도하는 중입니다…」를 알린다. 업로드는 명령 실행마다 키를 한 번 만들어 재시도에 재사용한다. **다른 쓰기(편집·되돌리기·태그·삭제·복원)는 재시도하지 않는다** — 서버가 키를 지키지 않는다. 예산을 넘기거나 재시도 대상이 아닌 503은 「서버가 일시적으로 응답하지 못했습니다. 잠시 후 다시 실행하세요.」로 실패한다.
- **D4 자격증명 파일** — `openarchive_home() / "credentials.json"`에 `{"url": "...", "token": "..."}`. **권한 600**으로 쓴다(새 파일이든 기존 파일이든 쓴 뒤 0600이어야 한다). 디렉터리가 없으면 0700으로 만든다(있는 디렉터리의 권한은 바꾸지 않는다). 서버는 하나만 기억하고 다시 로그인하면 덮어쓴다. `logout` 명령은 만들지 않는다.
- **D5 모듈 경계** — `backend/openarchive/client.py`(REST 호출·자격증명 읽기/쓰기·오류 변환·재시도)와 `backend/openarchive/user_cli.py`(사용자 명령 실행·출력). `cli.py`는 서브커맨드 등록과 분기만 한다. **사용자 경로는 DB에 붙지 않고 `get_settings()`·`DATABASE_URL`·`.env`를 읽지 않는다**(CLAUDE.md CRITICAL, ADR-057 결정 2). `client.py`·`user_cli.py`는 `openarchive.config`에서 `openarchive_home`만 가져오고 `openarchive.services`·`openarchive.db`·`psycopg`를 import하지 않는다.
- **D6 편집 충돌 문구** — `current_version` 키가 있는 409만 CLI가 「다른 곳에서 먼저 수정되었습니다」(+ 「현재 버전은 vN입니다」)로 출력한다. 서버 문구는 웹이 쓰므로 바꾸지 않는다.
- **D7 `doc list`** — 웹과 같은 정렬(`sort=updated`)로 `limit=100` 페이지를 끝까지(받은 수 < 100이 될 때까지) 받아 표로 출력: ID·제목·유형·버전·처리 상태. 처리 상태 라벨은 웹 `frontend/src/components/StatusBadge.tsx DocumentStatusBadge`와 같다 — `extraction_status`가 `pending`이면 「텍스트 인식 중」, `failed`면 「텍스트 인식 실패」, `done`이면 `embedding_status`로 `pending` 「대기 중」·`processing` 「처리 중…」·`ready` 「완료」·`error` 「실패」. 필터 옵션은 두지 않는다. 문서가 없으면 「문서가 없습니다.」.
- **D8 `search`/`ask` 분기** — 두 명령의 `--user`를 **선택 인자**로 바꾼다. `--user`가 있으면 기존 운영자 직결(그대로), 없으면 로그인 토큰으로 REST. `--dsn`만 있고 `--user`가 없으면 「--dsn은 --user와 함께 쓰는 운영자 옵션입니다.」로 종료 코드 2.
- **D9 테스트** — 사용자 CLI 테스트는 **실제 앱 + 실제 DB**(pgvector 컨테이너, `db_client` fixture)로 관통한다. 주입 지점은 `client.py`의 모듈 변수 하나(`TRANSPORT: httpx.BaseTransport | None = None`)이고, 테스트는 `monkeypatch.setattr(openarchive.client, "TRANSPORT", db_client._transport)`로 lifespan이 돈 앱에 요청을 보낸다(로그인 URL은 `http://testserver`). 응답을 흉내 내는 가짜 서버로 문서·권한 동작을 검증하지 마라. **예외는 D3 재시도 로직뿐**이다 — 503을 실제로 만들 수 없으므로 `httpx.MockTransport`로 상태 코드·헤더만 흉내 내고, 대기는 주입한 `sleep`으로 기록해 시간을 쓰지 않는다. 자격증명 위치는 `monkeypatch.setenv("OPENARCHIVE_HOME", str(tmp_path))`로 격리한다.
- **D10 `needs_vm=false`** — 서버 변경은 `/me` 필드뿐이고 OpenProxy 고유 동작이 없다.
- **D11 삭제** — ADR-060 결정 8: `doc delete <id>`는 **확인 없이 휴지통**으로 옮긴다(「휴지통으로 옮겼습니다. openarchive doc trash restore <id>로 되돌릴 수 있습니다.」). `doc delete <id> --permanent`는 「삭제하면 되돌릴 수 없습니다. 계속할까요? [y/N]」를 묻고 `y`(대소문자 무관)일 때만 영구 삭제한다. `doc trash list`·`doc trash restore <id>`.

**공통 출력·종료 규칙** (모든 사용자 명령):

- 성공 0, 실패 1, 잘못된 사용 2. 트레이스백을 내지 않는다.
- 로그인 정보가 없으면 「로그인이 필요합니다. openarchive login --url <서버> --token <API 토큰>으로 먼저 로그인하세요.」(1).
- 401(토큰 폐기·만료) → 「토큰이 올바르지 않습니다. openarchive login으로 다시 로그인하세요.」 / 연결 실패 → 「서버에 연결하지 못했습니다: <url>」 / 그 밖의 4xx → 서버 `detail` 그대로(404 「문서를 찾을 수 없습니다.」, 403 「쓰기 권한이 필요합니다.」 등).
- 문서 ID 인자는 UUID로 파싱한다. 형식이 틀리면 서버에 보내지 않고 「문서를 찾을 수 없습니다.」(1) — 존재 여부와 무관한 같은 문구.
- 메시지는 stdout, 재시도 안내만 stderr.

**세션 전용 동작은 만들지 않는다**(ADR-057 결정 3): 열람 범위 변경·폴더 열람 범위·외부 공유·토큰 발급/목록/폐기·`/api/admin/*`. 운영자 CLI(`init`·`create-user`·`serve`·`reset-password`·`rebuild-edges`·`reextract`·`import`·`export`·`demo`, `--user` 있는 `search`·`ask`)는 동작을 바꾸지 않는다.

### 완료 조건 — 이슈 #189 시험항목 14 (제출 명세서 TC는 이 중 5개, 문구가 구현 계약이다)

정본: 제출본 `notes/contest/submission/functional-spec-submit.md`(★ 표시가 제출 TC). 「」 안 문구는 글자 그대로 출력한다.

| 항목 | 시험 내용 |
|---|---|
| ★ 토큰 로그인 | `openarchive login --url http://<서버>:8000 --token <API 토큰>` → 「<사용자명>(으)로 로그인했습니다」, 토큰이 `~/.openarchive`에 권한 600으로 저장, `openarchive whoami`가 사용자명과 토큰 범위(read/read_write)(+ 만료일, #199)를 보임 |
| 잘못된 토큰 | 폐기했거나 틀린 토큰으로 `login` → 「토큰이 올바르지 않습니다」, 저장되지 않음 |
| 문서 목록 | `doc list` → 웹 문서 목록과 같은 문서(제목·유형·버전·처리 상태) 표 |
| 문서 보기 | `doc show <ID>` 현재 버전 텍스트, `--version 1`이면 v1 텍스트 |
| ★ 문서 올리기 | `doc upload 회의록.docx --title "10월 회의록" --tag 회의` → 문서 ID 출력, 웹 목록에 같은 제목·태그, 처리 후 검색됨 |
| ★ 텍스트 편집 | `doc edit <ID> --file 수정본.txt` → 새 버전 번호 출력, 버전 이력에 추가 |
| 편집 충돌 | `doc show`로 v1을 받은 뒤 웹에서 먼저 편집(v2) → `doc edit <ID> --file 수정본.txt --base-version 1` → 「다른 곳에서 먼저 수정되었습니다」로 거부, 웹 내용이 덮이지 않음 |
| 버전 되돌리기 | `doc restore <ID> 1` → v1 내용으로 새 버전, 이전 버전들은 이력에 남음 |
| 태그 편집 | `doc tag <ID> --set 인사,규정` → 태그가 「인사」「규정」으로 바뀜 |
| 원본 내려받기 | `doc download <ID> -o 받은파일.pdf` → sha256이 업로드 원본과 같음 |
| 문서 삭제 | (D11로 개정) `doc delete <ID>` → 휴지통, 웹 목록에서 사라짐 · `--permanent`는 「삭제하면 되돌릴 수 없습니다」 확인에 `y`를 넣어야 영구 삭제 |
| ★ 검색·답변 | 로그인 상태에서 `--user` 없이 `search "출장비 정산 기한"`·`ask "출장비 정산 기한은?"` → 토큰 주인이 볼 수 있는 문서만으로 결과·답변 |
| 열람 범위 | 다른 사용자의 비공개 문서 ID로 `doc show` → 「문서를 찾을 수 없습니다」 |
| ★ 읽기 전용 토큰 | read 토큰으로 로그인 → `doc edit`·`doc upload`·`doc delete`는 「쓰기 권한이 필요합니다」로 거부, `doc list`·`doc show`·`search`는 동작 |

## 읽어야 할 파일

- `phases/m30-user-cli/index.json` — step 0~2 summary
- `backend/openarchive/client.py`, `backend/openarchive/user_cli.py`, `backend/tests/test_user_cli.py`, `backend/tests/test_client.py` — 이전 step 산출물
- `backend/openarchive/api/documents.py` — `upload_document`(multipart 필드·`Idempotency-Key`), `edit_document`, `restore_document_version`, `update_tags`, `delete_document`(`permanent`), `list_trash`, `restore_document`
- `backend/openarchive/api/schemas.py` — `EditDocumentRequest`·`RestoreVersionRequest`·`UpdateTagsRequest`·`TrashItem`
- `backend/openarchive/main.py` — `VersionConflict` 409 응답(`current_version` 키)
- `/docs/ADR.md` — ADR-017(낙관적 잠금), ADR-037 결정 3(되돌리기는 새 버전), ADR-047(멱등키), ADR-060 결정 8
- `backend/tests/conftest.py` — `run_embedding_worker`, `upload_document`, `login_as`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_user_cli.py`에 추가

1. **문서 올리기**: `doc upload <tmp>/회의록.docx --title "10월 회의록" --tag 회의` (docx는 `tests/fixtures/`의 것을 복사하거나 `python-docx`로 만든다) → 0, 출력에 새 문서 ID. alice 세션의 `GET /api/documents`에 같은 ID·제목 「10월 회의록」·태그 `["회의"]`가 있다. `run_embedding_worker` 뒤 alice 토큰으로 `POST /api/search`(회의록 본문 단어)가 그 문서를 찾는다. `--tag`는 여러 번 줄 수 있다.
2. **멱등키**: 업로드 요청에 `Idempotency-Key` 헤더가 실린다 — 실제 트랜스포트(`db_client._transport`)를 감싸 요청을 기록만 하고 그대로 넘기는 래퍼로 확인한다(응답을 흉내 내지 않는다). 명령을 두 번 실행하면 키가 다르다.
3. 없는 파일 경로 → 2(또는 1)와 「파일이 없습니다: <경로>」, 서버에 요청하지 않는다. 지원하지 않는 형식은 서버 400 문구를 그대로 보인다.
4. **텍스트 편집**: `doc edit <ID> --file 수정본.txt` → 0, 출력에 새 버전 번호(`v2`). 상세의 `versions`에 그 버전이 추가되고 `content`가 파일 내용과 같다.
5. **편집 충돌**: `doc show`로 v1 텍스트를 받아 수정본을 만든다 → alice 세션으로 `PUT /api/documents/{id}`(version 1)로 먼저 편집(v2) → `doc edit <ID> --file 수정본.txt --base-version 1` → 1, 「다른 곳에서 먼저 수정되었습니다」와 「v2」. 현재 내용은 웹에서 쓴 내용 그대로이고 버전은 2다.
6. **버전 되돌리기**: v3까지 만든 뒤 `doc restore <ID> 1` → 0, 새 버전 v4가 출력되고 v4 내용이 v1과 같으며 `versions`에 1·2·3이 그대로 있다.
7. **태그 편집**: `doc tag <ID> --set 인사,규정` → 0, 상세의 태그가 `["인사","규정"]`(순서 무관)이다. 공백이 섞여도(`"인사, 규정"`) 같다.
8. **삭제(휴지통)**: `doc delete <ID>` → 0, 확인을 묻지 않고(`input`이 호출되면 실패하도록 막는다) 휴지통 안내가 나온다. alice 세션 목록에서 사라지고 `doc trash list`에 보인다. `doc trash restore <ID>` → 목록에 돌아온다.
9. **영구 삭제**: `doc delete <ID> --permanent`에 `input`이 `"n"`을 돌려주면 「삭제하면 되돌릴 수 없습니다」를 물은 뒤 아무것도 지우지 않는다(문서가 그대로). `"y"`면 영구 삭제되어 목록에도 휴지통에도 없다. 프롬프트 문구에 「삭제하면 되돌릴 수 없습니다」가 있다.
10. **읽기 전용 토큰**: read 토큰으로 로그인하고 `doc edit`·`doc upload`·`doc delete` → 각각 1, 「쓰기 권한이 필요합니다」, 문서 수·내용·버전 변화 없음. 같은 토큰으로 `doc list`·`doc show`는 동작한다.
11. **열람 범위**: bob 비공개 문서에 alice가 `doc edit`·`doc tag`·`doc delete` → 「문서를 찾을 수 없습니다」. 남의 공개 문서를 alice가 고치면 서버 403 문구가 그대로 나온다.

### 2) 구현

`user_cli.py`:

```python
def run_doc_upload(*, path: Path, title: str | None, tags: list[str]) -> int
def run_doc_edit(*, document_id: str, file: Path, base_version: int | None) -> int
def run_doc_restore(*, document_id: str, version: int) -> int
def run_doc_tag(*, document_id: str, tags_csv: str) -> int
def run_doc_delete(*, document_id: str, permanent: bool) -> int
def run_trash_list() -> int
def run_trash_restore(*, document_id: str) -> int
```

- **upload**: multipart `file`(파일명은 `path.name`), `title`(있을 때), `tags`(태그마다 같은 이름 필드 반복). 명령 실행마다 `uuid4()`로 `Idempotency-Key`를 만든다(step 1 클라이언트가 재시도에 같은 키를 재사용). 성공 출력: 「문서를 올렸습니다 — 처리가 끝나면 검색됩니다.」 다음 줄에 문서 ID.
- **edit**: 파일을 UTF-8로 읽는다(실패하면 「UTF-8 텍스트 파일만 쓸 수 있습니다.」 1). `--base-version`이 없으면 `GET /api/documents/{id}`의 `version`을 기준으로 쓴다. `PUT {content, version}`. 성공: 「저장했습니다 — 새 버전 v{N}」. `current_version`이 있는 409 → D6 문구 「다른 곳에서 먼저 수정되었습니다. 현재 버전은 v{M}입니다 — openarchive doc show로 다시 받아 고친 뒤 실행하세요.」(1). `current_version`이 없는 409는 서버 `detail` 그대로.
- **restore**: 현재 버전을 `GET`으로 읽어 `{current_version}`으로 `POST /versions/{n}/restore`. 성공: 「되돌렸습니다 — v{n} 내용으로 새 버전 v{N}」. 409는 edit와 같은 처리.
- **tag**: 쉼표로 나눠 공백 제거·빈 값 제외 후 `PUT /tags {tags}`. 성공: 「태그: 인사, 규정」(없으면 「태그를 모두 지웠습니다.」).
- **delete**: D11. 영구 삭제 확인은 `input()`으로 받고 EOF·`y` 외 입력은 「취소했습니다.」(1). 휴지통 이동 성공: 「휴지통으로 옮겼습니다. openarchive doc trash restore <ID>로 되돌릴 수 있습니다.」, 영구 삭제 성공: 「영구 삭제했습니다.」
- **trash list**: 열 ID·제목·삭제한 때·영구 삭제 예정(로컬 시간 `YYYY-MM-DD HH:MM`). 없으면 「휴지통이 비어 있습니다.」 **trash restore**: 「복원했습니다: <제목>」.

`cli.py`: step 2의 `doc` 하위 파서에 `upload`(`path`, `--title`, `--tag` 반복)·`edit`(`document_id`, `--file` 필수, `--base-version`)·`restore`(`document_id`, `version`)·`tag`(`document_id`, `--set` 필수)·`delete`(`document_id`, `--permanent`)·`trash`(하위 `list`·`restore document_id`)를 등록하고 분기만 한다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_user_cli.py tests/test_client.py tests/test_cli.py tests/test_token_access.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(테스트가 실패해야 한다, 확인 뒤 되돌린다): edit가 `--base-version`을 무시하고 늘 현재 버전을 보낸다(테스트 5) · `--permanent` 확인을 건너뛴다(테스트 9) · 휴지통 삭제에 `permanent=true`를 보낸다(테스트 8) · 업로드에 멱등키를 싣지 않는다(테스트 2).
3. 아키텍처 체크리스트: 쓰기 권한·소유자·열람 판정을 CLI가 미리 하지 않고 서버 응답을 전하는가, 쓰기 요청이 재시도되지 않는가(업로드 제외), 앱이 `embedding_jobs`·감사 테이블에 손대지 않는가(REST만 쓴다).
4. `phases/m30-user-cli/index.json`의 step 3을 갱신한다.

## 금지사항

- read 토큰을 CLI가 미리 막지 마라(예: `whoami`로 scope를 보고 거부). 이유: 판정은 서버 한 곳(`require_write_user_id`)이어야 웹·CLI가 같은 결과를 낸다(ADR-057 결정 1).
- `--base-version` 없는 편집에서 409를 받았을 때 자동으로 다시 읽어 재시도하지 마라. 이유: 남의 수정을 덮는다(ADR-017).
- `doc delete`(휴지통)에 확인을 넣지 마라. 이유: ADR-060 결정 8 — 되돌릴 수 있는 동작이다. 확인은 `--permanent`만.
- 열람 범위 변경·공유·폴더 범위·토큰 발급 명령을 만들지 마라. 이유: 세션 전용(ADR-057 결정 3).
- 기존 테스트를 깨뜨리지 마라
