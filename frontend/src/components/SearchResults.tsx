// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useId, useState } from "react";

import { passageHref } from "@/lib/passageLink";
import { relationLabel } from "@/lib/relations";
import type { SearchResponse } from "@/lib/types";
import { joinMeta, OwnerLabel } from "./OwnerLabel";

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
          <p className="text-sm text-muted">질문이나 키워드로 문서를 찾고, 근거와 함께 답을 받아 보세요.</p>
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

  const heading = (item: (typeof direct)[number]) => (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span data-testid="result-type" className="shrink-0 rounded bg-accent-soft px-1.5 py-0.5 text-xs font-medium text-accent">
          {item.content_type.toUpperCase()}
        </span>
        {/* 함께 볼 문서는 이전 판의 청크 번호가 현재 판 기준일 수 있어 문서로 연결한다. */}
        <Link href={item.via === null ? passageHref(item) : `/documents/${item.document_id}`} className="min-w-0 break-words font-semibold text-strong hover:text-accent">
          {item.title}
        </Link>
      </div>
      <p data-testid="result-meta" className="mt-1 break-words text-xs text-muted">
        {joinMeta([
          item.folder_path?.join(" › "),
          item.owner ? <OwnerLabel owner={item.owner} department={item.owner_department} /> : null,
          item.updated_at ? DATE_FORMATTER.format(new Date(item.updated_at)) : null,
          item.tags.length > 0 ? item.tags.join(", ") : null,
        ])}
      </p>
    </>
  );

  return (
    <section id="search-results" className="scroll-mt-6 space-y-6">
      {loading ? <p className="text-sm text-muted">검색 중…</p> : null}
      {error !== null ? <p className="text-sm text-failed" role="status">{error}</p> : null}

      {response.items.length === 0 ? (
        <p className="text-sm text-muted">검색 결과가 없습니다.</p>
      ) : null}

      {direct.length > 0 ? (
        <section aria-labelledby="direct-search-results" className="space-y-3">
          <h2 id="direct-search-results" className="text-lg font-semibold text-strong">검색 결과</h2>
          {direct.map((item) => (
            <article
              key={`${item.document_id}:${item.based_on_version}:${item.chunk_index}`}
              className="rounded-xl border border-line bg-card p-5"
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">{heading(item)}</div>
                <span className="shrink-0 text-xs text-muted">유사도 {item.score.toFixed(3)}</span>
              </div>
              <SearchExcerpt key={`${item.content}:${item.preview ?? ""}`} content={item.content} preview={item.preview} />
              {item.passages && item.passages.length > 0 ? (
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
          ))}
        </section>
      ) : null}

      <div>
        <button
          type="button"
          onClick={() => setShowSql((visible) => !visible)}
          className="text-sm text-muted hover:text-strong"
        >
          {showSql ? "실행된 SQL 닫기" : "실행된 SQL 보기"}
        </button>
        {showSql ? (
          <pre className="mt-2 overflow-x-auto rounded-lg bg-sunken p-4 font-mono text-xs text-body">
            {response.sql}
          </pre>
        ) : null}
      </div>

      {related.length > 0 ? (
        <section aria-labelledby="related-search-results" className="space-y-3 border-t border-line pt-6">
          <h2 id="related-search-results" className="text-lg font-semibold text-strong">함께 볼 문서</h2>
          <p className="text-sm text-muted">검색 결과에서 관계로 이어진 문서입니다. 각 문서의 연결 이유를 확인하세요.</p>
          <div data-testid="related-cards" className="grid gap-3 sm:grid-cols-2">
            {related.map((item) => (
              <article key={item.document_id} className="min-w-0 rounded-xl border border-line bg-card p-5">
                <p className="mb-2 text-xs text-muted">{item.via === null ? null : relationLabel(item.via.kind)}</p>
                {heading(item)}
                {item.via !== null ? (
                  <p className="mt-2 text-xs text-muted">
                    「{item.via.from_title}」에서 「{relationLabel(item.via.kind)}」로 이어짐
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}
