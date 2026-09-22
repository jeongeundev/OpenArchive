# Step 1: links-normalize

## 배경 — `[[…]]` 안을 통째로 제목으로 저장해 링크의 68%가 풀리지 않는다 (#93 L1·L2, #103)

`backend/migrations/012_links_triggers.sql`의 `replace_document_links()`는 본문에서
`\[\[([^\[\]\n]+)\]\]`를 찾아 안쪽 문자열을 그대로 `document_links.target_title`에 넣는다. Obsidian
문법은 안쪽에 세 가지를 더 담는다 — `[[제목|별칭]]`(`|` 뒤는 표시 문구), `[[제목#절]]`(`#` 뒤는 절),
`[[경로/제목]]`(`/` 앞은 폴더). 그리고 `![[첨부.png]]`는 링크가 아니라 임베드다.

코퍼스 A(Obsidian 한국어 도움말 176 + `docs/` 8) 링크 1,619행 중 미해결 1,095(68%), 그중 701(43%)은
`#`·`|`·경로를 떼면 존재하는 문서였다(alias 414 · section 287). 임베드 197건은 영구 미해결로 남았다.
재현: 「목록 뷰」의 `[[기본 서식 구문#목록|목록]]` → 「기본 서식 구문」.

저장 규칙은 DB 계층 한 곳에 둔다(ADR-030: 링크는 트리거가 만들고 애플리케이션은 읽기만 한다).
화면(`WikilinkContent.tsx`)이 같은 규칙을 복제해 해석 결과를 찾으므로 step 2가 그것을 맞춘다.
기존 행은 트리거가 다시 만들어 주지 않으므로(`AFTER INSERT OR UPDATE OF content_hash`) 012처럼
마이그레이션이 `document_links`를 전량 재생성한다.

## 읽어야 할 파일

- `CLAUDE.md` — "스키마 변경은 번호 붙은 raw SQL", "임시 테이블 금지", "`*_triggers.sql`은 `test_triggers.py`가 필요"
- `docs/ADR.md` **ADR-030** — 대상은 제목 문자열, 해석은 조회 시점, 깨진 링크 허용
- `backend/migrations/010_links_tables.sql` — `document_links` 스키마와 유니크 인덱스
- `backend/migrations/011_links_triggers.sql`·**`012_links_triggers.sql`** — 현재 규칙(개행 제외·큰따옴표 시작 제외)과
  전량 재생성 방식. 012 머리 주석의 "011을 고치지 않고 파일을 새로 두는 이유"가 이번에도 적용된다
- `backend/app/migrations.py` — 러너. 파일명 순서·`schema_migrations` 기록
- `backend/tests/test_triggers.py` — **테스트 추가 대상.** 헬퍼 `insert_document`·`edit_content`·`links_for`,
  기존 위키링크 테스트 4개(`test_wikilinks_are_stored_once_…` 이하)
- `backend/app/cli.py::_conflicting_tables` docstring — 012의 `DELETE FROM document_links`를 언급한다
- `frontend/src/components/WikilinkContent.tsx` — 읽기만. 012 규칙을 복제하는 곳이며 step 2가 고친다

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_triggers.py`

기존 위키링크 테스트 옆에 추가한다. 전부 `insert_document` → `links_for(conn, doc_id)`로 확인한다.

1. `test_alias_section_and_path_are_normalized_to_the_target_title` —
   `[[기본 서식 구문#목록|목록]]`·`[[Obsidian Web Clipper/템플릿|템플릿]]`·`[[내부 링크#헤딩으로 링크]]`·
   `[[폴더/하위/제목]]`을 담은 본문 → 저장된 제목은 정확히 `{"기본 서식 구문", "템플릿", "내부 링크", "제목"}`.
   순서: 별칭(`|`) 제거 → 절(`#`) 제거 → 마지막 `/` 앞 제거 → 양끝 공백 제거.
2. `test_links_that_normalize_to_the_same_title_are_stored_once` — `[[A|별칭]]`·`[[A#절]]`·`[[A]]`가 한 본문에
   있으면 `("A", None)` 한 행이다(유니크 인덱스에 걸리지 않고 `DISTINCT`로 접힌다).
3. `test_embeds_and_media_attachments_are_not_wikilinks` — `![[첨부.png]]`·`![[노트 임베드]]`·`[[그림.JPG]]`·
   `[[영상.mp4]]`는 저장되지 않는다. 같은 본문의 `[[운영 가이드]]`만 남는다.
4. `test_document_format_attachments_stay_as_written` — `[[규정집.pdf]]`·`[[자료.zip]]`은 **그대로** 저장된다
   (`"규정집.pdf"`, `"자료.zip"`). 이유: PDF·ZIP은 이 제품이 문서로 적재하는 형식이라 지우면 사람이 고칠 기회가 없다.
   미해결 링크로 남아 `/diagnostics`에 보이는 것이 맞다.
5. `test_section_only_and_alias_only_links_are_not_stored` — `[[#절만]]`·`[[|별칭만]]`·`[[폴더/]]`는 정규화 뒤
   빈 문자열이라 저장되지 않는다.
6. `test_wikilink_targets_function_is_the_single_source_of_the_rule` — 위 1~5의 본문을 하나로 합쳐
   `SELECT target FROM wikilink_targets(%s)`의 결과 집합이 트리거가 저장한 `links_for` 집합과 같다.
   전량 재생성(아래 3)이 같은 함수를 쓰므로, 이 동치가 백필의 정확성을 대신 보장한다.
7. 기존 위키링크 테스트 4개(한 번 저장·본문 교체 · 큰따옴표 시작·개행 제외 · 제목 안 큰따옴표 허용 · 삭제 CASCADE)는
   손대지 않고 통과해야 한다.

### 2) 마이그레이션 — `backend/migrations/015_links_triggers.sql`

머리 주석에 배경(위 수치 포함)·규칙·"012를 고치지 않고 새 파일을 두는 이유"를 적는다.

```sql
-- 규칙 한 곳: 본문에서 위키링크 대상 제목을 뽑는다. 트리거와 전량 재생성이 같이 쓴다.
CREATE FUNCTION wikilink_targets(content text) RETURNS SETOF text
  LANGUAGE sql IMMUTABLE STRICT AS $$ … $$;

CREATE OR REPLACE FUNCTION replace_document_links() RETURNS trigger …  -- wikilink_targets(NEW.content)를 쓴다

DELETE FROM document_links;
INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
SELECT d.id, NULL::int, t FROM documents d CROSS JOIN LATERAL wikilink_targets(d.content) AS t
ON CONFLICT DO NOTHING;
```

`wikilink_targets`의 규칙 — 여기서 벗어나면 안 된다:

- 매치는 `regexp_matches(content, '(!?)\[\[([^\[\]\n]+)\]\]', 'g')`. **lookbehind를 쓰지 마라** — 앞 그룹
  `(!?)`로 임베드를 잡는다. 화면(JS)이 같은 패턴을 복제해야 하는데 오래된 Safari가 lookbehind를 지원하지 않는다.
- 제외(원문 기준, 정규화 전): 그룹 1이 `!`(임베드) · `btrim(그룹 2)`가 빈 문자열 · `btrim(그룹 2)`가 `"`로 시작(012의
  JSON 리터럴 규칙, 그대로).
- 정규화: `btrim(그룹 2)` → `split_part(…, '|', 1)` → `split_part(…, '#', 1)` → `regexp_replace(…, '^.*/', '')` → `btrim`.
- 제외(정규화 후): 빈 문자열 · 미디어 확장자 `~* '\.(png|jpe?g|gif|svg|webp|mp4|mov|mp3)$'`. **pdf·zip·docx·md·txt는
  제외하지 않는다** — 문서 적재 형식이다.
- `DISTINCT`. 트리거는 `ON CONFLICT DO NOTHING`을 유지한다(유니크 인덱스 `uq_document_links_source_title_position`).
- 트리거 `trg_replace_document_links`는 함수를 이름으로 참조하므로 재생성하지 않는다(012와 같다).
- `cli.py::_conflicting_tables` docstring의 "012는 `DELETE FROM document_links`를"에 015를 더한다 — 그 함수가 지키는
  것은 "남의 테이블 위에 적용하면 데이터가 손상된다"이고 015도 같은 DELETE를 한다.

## Acceptance Criteria

```bash
cd backend && source .venv/bin/activate
ruff check .
pytest tests/test_triggers.py tests/test_migrations.py tests/test_tables.py -q
pytest tests/test_links.py tests/test_diagnostics.py tests/test_search.py -q   # 링크 조회·진단·refers 순회 유지
bash ../scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: 애플리케이션 코드에 `document_links` INSERT가 새로 생기지 않았는가
   (`test_architecture.py::test_application_code_does_not_insert_into_derived_tables`)? 마이그레이션은 번호 붙은
   raw SQL 파일 하나인가? 임시 테이블을 쓰지 않았는가?
3. `phases/m14-chunk-links/index.json`의 step 1을 갱신한다 — `summary`에 함수 이름 `wikilink_targets(text)`와
   정규화 순서·제외 목록을 적는다(step 2가 그대로 복제한다).

## 금지사항

- 011·012 파일을 수정하지 마라. 이유: 러너가 파일명으로 적용 이력을 남겨 이미 적용된 DB는 다시 읽지 않는다.
- `document_links`에 대상 문서 id나 별칭·절 컬럼을 추가하지 마라. 이유: ADR-030 — 대상은 제목 문자열이고 해석은
  조회 시점에 한다. 표시 문구(별칭)는 본문에 그대로 있으므로 화면이 본문에서 읽는다(step 2).
- 제목 중복(L3)이나 `#절` 앵커 해석을 시도하지 마라. 이유: #93에서 한계로 기록, #97 네임스페이스 논의.
- `WikilinkContent.tsx`를 여기서 고치지 마라. 이유: step 2의 범위다. 이 step이 끝난 시점에 화면과 DB 규칙이
  잠시 어긋나는 것은 phase 안에서 해소된다(squash merge로 main에는 함께 들어간다).
