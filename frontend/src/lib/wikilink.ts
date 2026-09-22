// `015_links_triggers.sql`의 `wikilink_targets` 복제. 한쪽만 바뀌면 API가 해석한
// 정상 링크를 화면이 깨진 링크로 그린다 (ADR-027). lookbehind 대신 앞 그룹으로
// 임베드를 잡는다 — 오래된 Safari.
export const WIKILINK_PATTERN = /(!?)\[\[([^\[\]\n]+)\]\]/g;

export interface ParsedWikilink {
  title: string;
  label: string;
}

const MEDIA_EXTENSION = /\.(png|jpe?g|gif|svg|webp|mp4|mov|mp3)$/i;

/** 매치 그룹(bang, raw)을 저장 규칙대로 정규화한다. 링크가 아니면 null. */
export function parseWikilink(bang: string, raw: string): ParsedWikilink | null {
  const trimmed = raw.trim();
  if (bang === "!" || trimmed === "" || trimmed.startsWith('"')) return null;

  const [target, alias] = trimmed.split("|");
  const title = target.split("#")[0].replace(/^.*\//, "").trim();
  if (title === "" || MEDIA_EXTENSION.test(title)) return null;

  return { title, label: alias?.trim() || trimmed };
}
