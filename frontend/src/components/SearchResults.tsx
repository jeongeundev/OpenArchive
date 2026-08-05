"use client";

import Link from "next/link";
import { useState } from "react";

import type { SearchResponse } from "@/lib/types";

const EXCERPT_LENGTH = 300;

export function SearchResults({
  response,
  loading,
  error,
}: {
  response: SearchResponse | null;
  loading: boolean;
  error: string | null;
}): React.ReactElement {
  const [showSql, setShowSql] = useState(false);

  if (response === null) {
    return (
      <div className="space-y-2">
        {loading ? <p className="text-sm text-neutral-500">검색 중…</p> : null}
        {error !== null ? <p className="text-sm text-[#ef4444]" role="status">{error}</p> : null}
        {!loading && error === null ? (
          <p className="text-sm text-neutral-500">검색어와 필터를 입력해 검색하세요.</p>
        ) : null}
      </div>
    );
  }

  return (
    <section className="space-y-4">
      {loading ? <p className="text-sm text-neutral-500">검색 중…</p> : null}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="status">{error}</p> : null}

      {response.items.length === 0 ? (
        <p className="text-sm text-neutral-500">검색 결과가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {response.items.map((item) => {
            const excerpt = item.content.length > EXCERPT_LENGTH
              ? `${item.content.slice(0, EXCERPT_LENGTH)}…`
              : item.content;
            return (
              <article key={item.document_id} className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <Link href={`/documents/${item.document_id}`} className="font-medium text-white hover:text-[#0ea5e9]">
                      {item.title}
                    </Link>
                    <p className="mt-2 text-xs text-neutral-500">
                      {item.content_type.toUpperCase()}
                      {item.tags.length > 0 ? ` · ${item.tags.join(", ")}` : ""}
                    </p>
                  </div>
                  <span className="shrink-0 text-sm font-medium text-[#0ea5e9]">
                    유사도 {item.score.toFixed(3)}
                  </span>
                </div>
                <p className="mt-4 text-sm leading-relaxed text-neutral-300">{excerpt}</p>
              </article>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowSql((visible) => !visible)}
        className="text-sm text-neutral-500 hover:text-neutral-300"
      >
        {showSql ? "실행된 SQL 닫기" : "실행된 SQL 보기"}
      </button>
      {showSql ? (
        <pre className="overflow-x-auto bg-neutral-900 p-4 font-mono text-xs text-neutral-300">
          {response.sql}
        </pre>
      ) : null}
    </section>
  );
}
