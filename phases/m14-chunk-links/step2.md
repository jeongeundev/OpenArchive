# Step 2: links-frontend

## 배경 — 화면이 DB의 링크 규칙을 복제한다. 규칙이 바뀌었으니 화면도 맞춘다

step 1(`backend/migrations/015_links_triggers.sql`)이 위키링크 저장 규칙을 바꿨다 — `[[제목#절|별칭]]`·
`[[경로/제목]]`은 **제목**으로 정규화해 저장하고, `![[…]]` 임베드와 미디어 첨부(`.png` 등)는 저장하지 않는다.

`frontend/src/components/WikilinkContent.tsx`는 본문에서 `[[…]]`를 찾아 `GET /api/documents/{id}/links`가 준
해석 결과(`ResolvedLink.title` = 저장된 `target_title`)와 **제목 문자열로** 대조한다. 지금은 `match[1].trim()`을
그대로 키로 쓰므로, 015 뒤에는 `[[기본 서식 구문#목록|목록]]`을 `기본 서식 구문#목록|목록`으로 찾아 못 찾고
**깨진 링크로 그린다** — API가 해석해 준 정상 링크가 화면에서 깨진다. 012 주석과 이 컴포넌트 머리 주석이
경고한 바로 그 어긋남이다(ADR-027: 볼 수 있는 문서가 없는 문서처럼 보이면 안 된다). `![[첨부.png]]`도
지금은 깨진 링크 모양(점선 밑줄)으로 그려진다.

## 읽어야 할 파일

- `CLAUDE.md` — 프론트 스택(Next.js App Router·TypeScript strict·Tailwind), 테스트 규칙
- `docs/UI_GUIDE.md` 「관계 종류 어휘」·깨진 링크 표현 — 깨진 링크는 **사유 없이** 다른 모양이어야 한다(ADR-027)
- `docs/ADR.md` **ADR-030**·**ADR-027**
- `backend/migrations/015_links_triggers.sql` — **복제할 규칙의 원본.** `wikilink_targets`의 패턴·제외·정규화 순서
- `frontend/src/components/WikilinkContent.tsx` — **수정 대상**
- `frontend/src/components/WikilinkContent.test.tsx` — **테스트 추가 대상.** 기존 테스트의 render/assert 관용
- `frontend/src/lib/types.ts::ResolvedLink`, `frontend/src/lib/relations.ts`(+`.test.ts`) — `lib/` 순수 함수·테스트 파일 관례
- `frontend/src/components/TextEditor.tsx:126` — 유일한 사용처. props(`content`, `links`)는 바꾸지 않는다

## 작업

### 1) 테스트를 먼저 쓴다

`frontend/src/lib/wikilink.test.ts` (새 파일) — 순수 함수 `parseWikilink`:

1. `[[기본 서식 구문#목록|목록]]` → `{ title: "기본 서식 구문", label: "목록" }`
2. `[[Obsidian Web Clipper/템플릿|템플릿]]` → `{ title: "템플릿", label: "템플릿" }`; `[[폴더/제목]]` → `{ title: "제목", label: "폴더/제목" }`
   (별칭이 없으면 표시 문구는 **원문 그대로**)
3. `[[내부 링크#헤딩]]` → `{ title: "내부 링크", label: "내부 링크#헤딩" }`
4. 임베드 `![[첨부.png]]`·`![[노트]]`, 미디어 `[[그림.JPG]]`, `[[#절만]]`, `[[|별칭만]]`, `[[""]]`처럼 큰따옴표로
   시작하는 것 → `null`
5. `[[규정집.pdf]]` → `{ title: "규정집.pdf", label: "규정집.pdf" }` (문서 형식은 제외하지 않는다 — 015와 같다)
6. 양끝 공백: `[[ 제목 ]]` → `title: "제목"` (트리거의 `btrim`과 같다)

`frontend/src/components/WikilinkContent.test.tsx`에 추가:

7. 별칭 링크 — `content="[[기본 서식 구문#목록|목록]]을 보세요."`, `links=[{ title: "기본 서식 구문", document_id: "fmt-1" }]`
   → `getByRole("link", { name: "목록" })`의 href가 `/documents/fmt-1`. 본문 나머지("을 보세요.")가 남는다.
8. 경로 링크 — `[[Obsidian Web Clipper/템플릿]]`이 `title: "템플릿"` 해석 결과로 링크되고 표시 문구는 원문
   `Obsidian Web Clipper/템플릿`.
9. 임베드 — `content="그림 ![[첨부.png]] 아래 [[운영 가이드]]"`에서 `![[첨부.png]]`는 **원문 텍스트 그대로**
   남고(링크도 점선 span도 아님), `[[운영 가이드]]`만 링크/깨진 링크로 처리된다. `queryByText("첨부.png")`가
   `border-dashed` 클래스를 갖지 않는다.
10. 기존 테스트 9개(정상·깨진·동명 다중·양끝 공백·공백뿐·JSON 배열·제목 안 큰따옴표·여러 줄·HTML 텍스트)는 손대지 않고 통과한다. `links === null`이면 본문만 그리는 동작도 그대로다.

### 2) 구현

`frontend/src/lib/wikilink.ts` (새 파일):

```ts
// 015_links_triggers.sql `wikilink_targets`의 복제. 한쪽만 바뀌면 API가 해석한 정상 링크를
// 화면이 깨진 링크로 그린다 (ADR-027). lookbehind 대신 앞 그룹으로 임베드를 잡는다 — 오래된 Safari.
export const WIKILINK_PATTERN = /(!?)\[\[([^\[\]\n]+)\]\]/g;

export interface ParsedWikilink { title: string; label: string }

/** 매치 그룹(bang, raw)을 저장 규칙대로 정규화한다. 링크가 아니면 null. */
export function parseWikilink(bang: string, raw: string): ParsedWikilink | null
```

- 규칙은 015와 **문자 그대로** 같아야 한다: `bang === "!"` → null · `raw.trim()`이 빈 문자열이거나 `"`로 시작 → null ·
  정규화 `trim → split("|")[0] → split("#")[0] → 마지막 "/" 뒤 → trim` · 빈 문자열 또는
  `/\.(png|jpe?g|gif|svg|webp|mp4|mov|mp3)$/i` → null.
- `label`: `|` 뒤 문구를 trim한 것이 비어 있지 않으면 그것, 아니면 `raw.trim()`(원문 표기).
- `WikilinkContent.tsx`는 `WIKILINK_PATTERN`·`parseWikilink`를 쓴다. `null`이면 지금의 `continue`와 같이 원문
  구간을 그대로 남긴다. 링크·깨진 링크의 표시 문구는 `label`, 해석 결과 조회 키는 `title`.
- 머리 주석의 "012_links_triggers.sql" 참조를 015로 바꾸고, 규칙 복제 경고는 유지한다.
- 깨진 링크의 모양(점선 밑줄, 사유 없음)과 `links === null` 처리는 바꾸지 않는다.

## Acceptance Criteria

```bash
cd frontend
npm run lint
npx tsc --noEmit         # strict 타입 검사 (next build도 하지만 여기서 먼저 잡는다)
npm test
bash ../scripts/check.sh      # build:static이 backend/app/static을 다시 만든다 — 그 변경도 이 step의 산출물이다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: `parseWikilink`의 제외 목록·정규화 순서가 015의 `wikilink_targets`와 한 글자도 다르지 않은가
   (두 파일을 나란히 놓고 대조하라)? 깨진 링크에 사유(`title`·`aria-label`)를 붙이지 않았는가(ADR-027)?
3. `phases/m14-chunk-links/index.json`의 step 2를 갱신한다.

## 금지사항

- API나 `ResolvedLink` 타입에 별칭·절 필드를 추가하지 마라. 이유: 표시 문구는 본문에 있고 화면이 본문에서 읽는다.
  DB는 제목만 저장한다(ADR-030).
- 정규식에 lookbehind(`(?<!!)`)를 쓰지 마라. 이유: 015가 같은 이유로 앞 그룹을 쓴다 — 두 복제본의 패턴이 같아야 한다.
- 임베드를 이미지로 렌더하지 마라. 이유: 원본 파일을 보관하지 않는다(ADR-017). 원문 텍스트로 남긴다.
- `backend/app/static`을 손으로 고치지 마라. 이유: `npm run build:static`의 산출물이다.
