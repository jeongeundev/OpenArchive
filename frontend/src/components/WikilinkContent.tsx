import Link from "next/link";
import { Fragment } from "react";

import type { ResolvedLink } from "@/lib/types";

// `011_links_triggers.sql`의 `regexp_matches(content, '\[\[([^\[\]]+)\]\]', 'g')`와
// 같은 것을 링크로 본다. 한쪽만 달라지면 API가 해석해 준 정상 링크를 화면이 깨진 링크로
// 그리거나 그 반대가 된다 — 볼 수 있는 문서가 없는 문서처럼 보인다 (ADR-027).
const WIKILINK_PATTERN = /\[\[([^\[\]]+)\]\]/g;

export function WikilinkContent({
  content,
  links,
}: {
  content: string;
  links: ResolvedLink[] | null;
}): React.ReactElement {
  // null은 "해석 결과가 아직 없다"(로딩 중이거나 조회 실패)이고 []는 "링크가 없다"이다.
  // 둘을 뭉쳐 []로 다루면 본문의 모든 링크가 깨진 링크로 그려지는데, 깨짐과 비공개를
  // 구분되지 않게 만든 탓에(ADR-027) 사용자가 오해를 되돌릴 단서가 없다.
  if (links === null) return <>{content}</>;

  // Map.groupBy(ES2024) 대신 RelatedDocuments와 같은 reduce 관용을 쓴다 — 런타임
  // 메서드라 다운레벨로 채워지지 않고 폴리필도 없어 오래된 Safari에서 페이지가 죽는다.
  const targetsByTitle = links.reduce((byTitle, link) => {
    const group = byTitle.get(link.title) ?? [];
    group.push(link);
    byTitle.set(link.title, group);
    return byTitle;
  }, new Map<string, ResolvedLink[]>());
  const parts: React.ReactNode[] = [];
  let previousEnd = 0;

  for (const match of content.matchAll(WIKILINK_PATTERN)) {
    // 트리거가 `btrim` 뒤에 저장하므로 API가 주는 title에는 양끝 공백이 없다.
    const title = match[1].trim();
    // 트리거의 `WHERE btrim(match[1]) <> ''`에 대응한다. previousEnd를 그대로 두면
    // 다음 조각에 이 구간이 함께 실려 본문이 원문대로 남는다.
    if (title === "") continue;

    const start = match.index;
    parts.push(content.slice(previousEnd, start));

    const targets = (targetsByTitle.get(title) ?? []).filter(
      (target): target is ResolvedLink & { document_id: string } =>
        target.document_id !== null,
    );
    if (targets.length === 0) {
      parts.push(
        <span
          className="border-b border-dashed border-neutral-600 text-neutral-500"
          key={`${start}-${title}`}
        >
          {title}
        </span>,
      );
    } else {
      parts.push(
        <span key={`${start}-${title}`}>
          {targets.map((target, index) => (
            <Fragment key={target.document_id}>
              {index > 0 ? " · " : null}
              <Link
                className="text-[#0ea5e9] hover:underline"
                href={`/documents/${target.document_id}`}
              >
                {title}
              </Link>
            </Fragment>
          ))}
        </span>,
      );
    }
    previousEnd = start + match[0].length;
  }

  parts.push(content.slice(previousEnd));
  return <>{parts}</>;
}
