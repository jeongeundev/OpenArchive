// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useId, useState } from "react";

import { passageHref } from "@/lib/passageLink";
import { relationLabel } from "@/lib/relations";
import type { SearchResponse } from "@/lib/types";

const EXCERPT_LENGTH = 300;
const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

function SearchExcerpt({ content, preview }: { content: string; preview?: string | null }): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const collapsed = preview ?? (content.length > EXCERPT_LENGTH ? `${content.slice(0, EXCERPT_LENGTH)}…` : content);
  const compact = collapsed.replace(/\s+/g, " ").trim();
  const long = content !== compact;
  return (
    <>
      <p id={id} className={`mt-4 text-sm leading-relaxed text-body ${expanded ? "whitespace-pre-wrap" : "line-clamp-3"}`}>
        {expanded ? content : compact}
      </p>
      {long ? (
        <button type="button" aria-expanded={expanded} aria-controls={id}
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 text-sm text-accent hover:underline">
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
        {loading ? <p className="text-sm text-muted">검색 중…</p> : null}
        {error !== null ? <p className="text-sm text-failed" role="status">{error}</p> : null}
        {!loading && error === null ? (
          <p className="text-sm text-muted">검색어와 필터를 입력해 검색하세요.</p>
        ) : null}
      </div>
    );
  }

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
      {loading ? <p className="text-sm text-muted">검색 중…</p> : null}
      {error !== null ? <p className="text-sm text-failed" role="status">{error}</p> : null}

      {response.items.length === 0 ? (
        <p className="text-sm text-muted">검색 결과가 없습니다.</p>
      ) : (
        groups.filter((group) => group.items.length > 0).map((group) => (
          <section key={group.id} aria-labelledby={group.id} className="space-y-3">
            <h2 id={group.id} className="font-medium text-strong">{group.title}</h2>
            {group.id === "related-search-results" ? (
              <p className="text-sm text-muted">검색 결과에서 관계로 이어진 문서입니다. 각 문서의 연결 이유를 확인하세요.</p>
            ) : null}
            {group.items.map((item) => {
              const sourceTitle = item.via === null ? null : `「${item.via.from_title}」`;
              return (
                <article
                  key={`${item.document_id}:${item.based_on_version}:${item.chunk_index}`}
                  className="rounded-lg border border-line bg-card p-6"
                >
                  {/* 함께 볼 문서는 이전 판의 청크 번호가 현재 판 기준일 수 있어 문서로 연결한다. */}
                  <Link href={item.via === null ? passageHref(item) : `/documents/${item.document_id}`} className="break-words font-medium text-strong hover:text-accent">
                    {item.title}
                  </Link>
                  <p className="mt-2 break-words text-xs text-muted">
                    {[
                      item.content_type.toUpperCase(),
                      item.folder_path?.join(" › "),
                      item.owner,
                      item.updated_at ? DATE_FORMATTER.format(new Date(item.updated_at)) : null,
                      item.tags.length > 0 ? item.tags.join(", ") : null,
                    ].filter(Boolean).join(" · ")}
                    {item.via === null ? <> · <span className="text-muted">유사도 {item.score.toFixed(3)}</span></> : null}
                  </p>
                  {item.via !== null ? (
                    <p className="mt-3 text-xs text-muted">
                      {sourceTitle}에서 「{relationLabel(item.via.kind)}」로 이어짐
                    </p>
                  ) : null}
                  <SearchExcerpt key={`${item.content}:${item.preview ?? ""}`} content={item.content} preview={item.preview} />
                  {item.via === null && item.passages && item.passages.length > 0 ? (
                    <details className="mt-4 border-t border-line pt-3">
                      <summary className="cursor-pointer text-sm text-accent">
                        검색된 본문 대목 {item.passages.length}개
                      </summary>
                      <div className="mt-3 space-y-4">
                        {item.passages.map((passage, index) => (
                          <section key={`${passage.chunk_index}:${passage.based_on_version}`}>
                            <p className="text-xs text-muted">
                              본문 대목 {index + 1} · 텍스트 버전 {passage.based_on_version}
                            </p>
                            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-body">
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
        className="text-sm text-muted hover:text-strong"
      >
        {showSql ? "실행된 SQL 닫기" : "실행된 SQL 보기"}
      </button>
      {showSql ? (
        <pre className="overflow-x-auto bg-card p-4 font-mono text-xs text-body">
          {response.sql}
        </pre>
      ) : null}
    </section>
  );
}
