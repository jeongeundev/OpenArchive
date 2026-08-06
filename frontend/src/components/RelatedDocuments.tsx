import Link from "next/link";

import type { RelatedResponse } from "@/lib/types";

export function RelatedDocuments({
  response,
}: {
  response: RelatedResponse;
}): React.ReactElement {
  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">관련 문서</h2>
      {response.reason === "not_indexed" ? (
        <p className="text-sm text-neutral-500">임베딩이 완료되면 표시됩니다.</p>
      ) : (
        <>
          {response.identical.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-neutral-300">동일한 텍스트의 문서가 있습니다</p>
              <ul className="space-y-1">
                {response.identical.map((item) => (
                  <li key={item.document_id}>
                    <Link
                      href={`/documents/${item.document_id}`}
                      className="text-sm text-neutral-300 hover:text-[#0ea5e9]"
                    >
                      {item.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {response.items.length === 0 && response.identical.length === 0 ? (
            <p className="text-sm text-neutral-500">관련 문서가 없습니다.</p>
          ) : null}
        </>
      )}
      {response.reason !== "not_indexed" && response.items.length > 0 ? (
        <div className="space-y-3">
          <p className="text-sm text-neutral-300">내용이 유사한 문서가 있습니다</p>
          <ul className="divide-y divide-neutral-800 rounded-lg border border-neutral-800 bg-[#141414] px-5">
            {response.items.map((item) => (
              <li key={item.document_id} className="space-y-2 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <Link
                    href={`/documents/${item.document_id}`}
                    className="text-sm text-white hover:text-[#0ea5e9]"
                  >
                    {item.title}
                  </Link>
                  <div className="flex gap-3 text-xs text-neutral-500">
                    <span className="text-[#0ea5e9]">{item.score.toFixed(3)}</span>
                    <span>v{response.based_on_version} 기준</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {item.tags.map((tag) => (
                    <span key={tag} className="rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-300">
                      {tag}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
