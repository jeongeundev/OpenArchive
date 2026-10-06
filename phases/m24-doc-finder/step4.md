# Step 4: finder-docs

문서 목록 찾기(제목 검색·유형/태그 필터·정렬·조건 건수)를 운영 문서에 반영한다. 코드는 고치지 않는다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 0~3이 만든 것:
  - 서비스: `list_documents(..., title_query, content_type, sort="updated")`, `count_documents`, `list_visible_tags` — 목록과 건수가 같은 WHERE 조각을 쓰고, 열람 술어가 건수·태그 목록에도 걸린다. 기본 정렬이 `created_at`에서 **`updated_at` 최신순**으로 바뀌었다.
  - API: `GET /api/documents`에 `q`·`content_type`·`sort`, 새 `GET /api/documents/count` → `{total}`, `GET /api/documents/tags` → `string[]`
  - 화면: 목록 상단 `DocumentFilters`(제목 검색·유형·태그·정렬), 페이지 나눔은 조건 건수, 빈 결과 문구 "조건에 맞는 문서가 없습니다."
- 이슈 #187의 앞부분이다. 폴더는 다음 phase(m25-folders)가 하며 ADR-054는 그때 확정한다 — 이 step에서 ADR-054를 고치지 않는다.

## 읽어야 할 파일

- `/docs/ARCHITECTURE.md` — 문서 목록·API 절
- `/docs/UI_GUIDE.md` — 문서 목록 화면 절
- `/docs/OPERATIONS.md` — API·CLI 표가 있다면 그 절
- `/docs/PRD.md` — 문서 목록 기능 서술
- step 0~3에서 바뀐 파일: `backend/openarchive/services/documents.py`, `backend/openarchive/api/documents.py`, `frontend/src/components/DocumentFilters.tsx`, `frontend/src/app/page.tsx`

## 작업

- `ARCHITECTURE.md`: 문서 목록 쿼리가 열람 술어 + 정형 필터 + 제목 `ILIKE`(와일드카드 이스케이프)를 한 SQL로 걸고, 건수는 같은 WHERE로 따로 센다는 것. 기본 정렬 `updated_at DESC, id`. 새 엔드포인트 2개.
- `UI_GUIDE.md`: 목록 상단 찾기 컨트롤의 배치·라벨(「제목 검색」, 유형 「전체」+대문자 유형, 태그 「전체」+목록, 정렬 「최근 수정순」(기본)/「제목순」), 빈 결과 문구와 첫 사용 안내의 구분.
- `PRD.md`·`OPERATIONS.md`: 해당 서술이 있으면 맞춘다(없으면 더하지 않는다).
- 문구는 기존 문서의 어조(한국어 서술체, 근거 함께)를 따른다.

## Acceptance Criteria

```bash
grep -n "조건에 맞는 문서가 없습니다" docs/UI_GUIDE.md
grep -n "documents/count" docs/ARCHITECTURE.md
git diff --stat -- backend frontend   # 출력 없음(코드 변경 없음)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다 — 앞의 둘은 한 줄 이상 나와야 하고, 마지막은 비어 있어야 한다.
2. 문서의 서술이 실제 코드(함수 이름·엔드포인트·기본 정렬)와 맞는지 대조한다.
3. `phases/m24-doc-finder/index.json`의 step 4를 갱신한다.

## 금지사항

- 사용자 대상 문구에 "항상 최신"·"실시간"을 쓰지 마라. 이유: CLAUDE.md — 보장 범위는 버전 일관성 + 최신 수렴이다(목록은 2초 폴링).
- ADR-054·폴더 서술을 고치지 마라. 이유: 폴더는 m25-folders의 범위다.
- 코드 파일을 고치지 마라
