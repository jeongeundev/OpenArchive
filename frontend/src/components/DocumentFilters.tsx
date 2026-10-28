// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import type { DocumentFilters as Filters, DocumentSort } from "@/lib/api";
import { type ContentType } from "@/lib/types";

const CONTROL_CLASS = "h-11 rounded-lg border border-line bg-card text-sm text-strong";

export function DocumentFilters({ value, tags, types = [], onChange }: {
  value: Filters;
  tags: string[];
  types?: ContentType[];
  onChange: (value: Filters) => void;
}): React.ReactElement {
  const [query, setQuery] = useState(value.q ?? "");
  // 조건은 주소에서 온다. 바깥에서 바뀐 제목(뒤로 가기·「문서」 링크)은 입력칸에 반영하되, 내가 보낸 값이
  // 돌아온 것이면 그사이 이어서 친 글자를 지우지 않는다. 반영하지 않으면 남은 입력이 300ms 뒤 주소를 되돌린다.
  const [seenQ, setSeenQ] = useState(value.q ?? "");
  const [sentQ, setSentQ] = useState<string | null>(null);
  if ((value.q ?? "") !== seenQ) {
    setSeenQ(value.q ?? "");
    if ((value.q ?? "") !== sentQ) setQuery(value.q ?? "");
  }

  useEffect(() => {
    if (query === (value.q ?? "")) return;
    const timer = window.setTimeout(() => {
      setSentQ(query);
      onChange({ ...value, q: query });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, value, onChange]);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-60 flex-1">
        <svg aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4 4" /></svg>
        <input aria-label="제목 검색" placeholder="제목으로 찾기" className={`${CONTROL_CLASS} w-full pr-3 pl-10`} value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      <div role="group" aria-label="문서 유형" className="flex flex-wrap items-center gap-2">
        {[undefined, ...types].map(type => <button key={type ?? "all"} type="button" aria-pressed={(value.contentType ?? undefined) === type}
          onClick={() => onChange({ ...value, contentType: type })}
          className={`h-10 rounded-full border px-4 text-sm font-medium ${value.contentType === type ? "border-strong bg-strong text-on-strong" : "border-line bg-card text-body hover:text-strong"}`}>{type?.toUpperCase() ?? "전체"}</button>)}
      </div>
      <select aria-label="태그" className={`${CONTROL_CLASS} px-3`} value={value.tag ?? ""} onChange={(event) => onChange({ ...value, tag: event.target.value || undefined })}>
        <option value="">모든 태그</option>
        {tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
      </select>
      <select aria-label="정렬" className={`${CONTROL_CLASS} px-3`} value={value.sort ?? "updated"} onChange={(event) => onChange({ ...value, sort: event.target.value as DocumentSort })}>
        <option value="updated">최근 수정순</option>
        <option value="title">제목순</option>
      </select>
    </div>
  );
}
