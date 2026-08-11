import Link from "next/link";
import { Fragment } from "react";

import type { ResolvedLink } from "@/lib/types";

const WIKILINK_PATTERN = /\[\[([^\[\]\n]+)\]\]/g;

export function WikilinkContent({
  content,
  links,
}: {
  content: string;
  links: ResolvedLink[];
}): React.ReactElement {
  const targetsByTitle = Map.groupBy(links, (link) => link.title);
  const parts: React.ReactNode[] = [];
  let previousEnd = 0;

  for (const match of content.matchAll(WIKILINK_PATTERN)) {
    const start = match.index;
    const title = match[1];
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
