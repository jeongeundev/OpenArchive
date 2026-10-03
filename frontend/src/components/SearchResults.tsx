"use client";

import Link from "next/link";
import { useId, useState } from "react";

import { relationLabel } from "@/lib/relations";
import type { SearchResponse } from "@/lib/types";

const EXCERPT_LENGTH = 300;

function SearchExcerpt({ content, preview }: { content: string; preview?: string | null }): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const collapsed = preview ?? (content.length > EXCERPT_LENGTH ? `${content.slice(0, EXCERPT_LENGTH)}…` : content);
  const long = content !== collapsed;
  return (
    <>
      <p id={id} className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-neutral-300">
        {expanded ? content : collapsed}
      </p>
      {long ? (
        <button type="button" aria-expanded={expanded} aria-controls={id}
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 text-sm text-[#0ea5e9] hover:underline">
          {expanded ? "발췌 접기" : "발췌 더보기"}
        </button>
      ) : null}
    </>
  );
}

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

  const titlesById = new Map(
    response.items.map((item) => [item.document_id, item.title]),
  );

  const direct = response.items.filter((item) => item.via === null);
  const directIds = new Set(direct.map((item) => item.document_id));
  const seenRelated = new Set<string>();
  const related = response.items.filter((item) => {
    if (item.via === null || directIds.has(item.document_id) || seenRelated.has(item.document_id)) {
      return false;
    }
    seenRelated.add(item.document_id);
    return true;
  });
  const groups = [
    { id: "direct-search-results", title: "검색 결과", items: direct },
    { id: "related-search-results", title: "함께 볼 문서", items: related },
  ];

  return (
    <section className="space-y-4">
      {loading ? <p className="text-sm text-neutral-500">검색 중…</p> : null}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="status">{error}</p> : null}

      {response.items.length === 0 ? (
        <p className="text-sm text-neutral-500">검색 결과가 없습니다.</p>
      ) : (
        groups.filter((group) => group.items.length > 0).map((group) => (
          <section key={group.id} aria-labelledby={group.id} className="space-y-3">
            <h2 id={group.id} className="font-medium text-white">{group.title}</h2>
            {group.id === "related-search-results" ? (
              <p className="text-sm text-neutral-400">검색 결과와 연결된 문서입니다. 각 문서의 연결 이유를 확인하세요.</p>
            ) : null}
            {group.items.map((item) => {
              const sourceTitle = item.via === null
                ? null
                : titlesById.get(item.via.from_document_id) ?? "연결된 문서";
              return (
                <article
                  key={`${item.document_id}:${item.based_on_version}:${item.chunk_index}`}
                  className="rounded-lg border border-neutral-800 bg-[#141414] p-6"
                >
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
                    {/* 확장 결과의 score는 진입점까지의 거리에 단계 페널티를 더한 값이라
                        질의와의 유사도가 아니다. 직접 매칭과 같은 축에 세우면 안 된다. */}
                    {item.via === null ? (
                      <span className="shrink-0 text-sm font-medium text-[#0ea5e9]">
                        유사도 {item.score.toFixed(3)}
                      </span>
                    ) : null}
                  </div>
                  {item.via !== null ? (
                    <p className="mt-3 text-xs text-neutral-400">
                      {sourceTitle}에서 「{relationLabel(item.via.kind)}」로 이어짐
                    </p>
                  ) : null}
                  <SearchExcerpt key={`${item.content}:${item.preview ?? ""}`} content={item.content} preview={item.preview} />
                  {item.via === null && item.passages && item.passages.length > 0 ? (
                    <details className="mt-4 border-t border-neutral-800 pt-3">
                      <summary className="cursor-pointer text-sm text-[#0ea5e9]">
                        검색된 본문 대목 {item.passages.length}개
                      </summary>
                      <div className="mt-3 space-y-4">
                        {item.passages.map((passage, index) => (
                          <section key={`${passage.chunk_index}:${passage.based_on_version}`}>
                            <p className="text-xs text-neutral-500">
                              본문 대목 {index + 1} · 텍스트 버전 {passage.based_on_version}
                            </p>
                            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-neutral-300">
                              {passage.content}
                            </p>
                          </section>
                        ))}
                      </div>
                    </details>
                  ) : null}
                </article>
              );
            })}
          </section>
        ))
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
