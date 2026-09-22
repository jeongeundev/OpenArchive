// `015_links_triggers.sql`의 `wikilink_targets` 복제. 한쪽만 바뀌면 API가 해석한
// 정상 링크를 화면이 깨진 링크로 그린다 (ADR-027). lookbehind 대신 앞 그룹으로
// 임베드를 잡는다 — 오래된 Safari.
export const WIKILINK_PATTERN = /(!?)\[\[([^\[\]\n]+)\]\]/g;

export interface ParsedWikilink {
  title: string;
  label: string;
}

const MEDIA_EXTENSION = /\.(png|jpe?g|gif|svg|webp|mp4|mov|mp3)$/i;

// 015의 `btrim(text)`는 공백 문자(U+0020)만 뗀다. `String.prototype.trim()`은 탭·NBSP까지
// 지워 `[[\t제목]]`을 저장 제목과 다른 제목으로 찾게 된다 — 저장 규칙 쪽에 맞춘다.
const trimSpaces = (value: string): string => value.replace(/^ +| +$/g, "");

/** 매치 그룹(bang, raw)을 저장 규칙대로 정규화한다. 링크가 아니면 null. */
export function parseWikilink(bang: string, raw: string): ParsedWikilink | null {
  const trimmed = trimSpaces(raw);
  // 큰따옴표는 시작 위치만 본다. 제목 안의 큰따옴표까지 막으면 `ADR-015: 제품은
  // "AI를 위한 문서 저장소"이며, …` 같은 실재하는 제목이 깨진 링크가 된다 (#49).
  if (bang === "!" || trimmed === "" || trimmed.startsWith('"')) return null;

  // 015의 `split_part(…, '|', 1)`처럼 첫 `|`에서 자른다. 뒤는 전부 표시 문구다.
  const bar = trimmed.indexOf("|");
  const target = bar < 0 ? trimmed : trimmed.slice(0, bar);
  const alias = bar < 0 ? "" : trimmed.slice(bar + 1);
  const title = trimSpaces(target.split("#")[0].replace(/^.*\//, ""));
  if (title === "" || MEDIA_EXTENSION.test(title)) return null;

  return { title, label: trimSpaces(alias) || trimSpaces(target) };
}
