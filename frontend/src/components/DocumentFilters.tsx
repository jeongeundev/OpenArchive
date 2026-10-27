// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import type { DocumentFilters as Filters, DocumentSort } from "@/lib/api";
import { type ContentType } from "@/lib/types";

const INPUT_CLASS = "mt-1 w-full rounded-lg border border-line bg-card px-3 py-1.5 text-sm text-strong";

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
    <div className="grid gap-3 rounded-lg border border-line bg-card p-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
      <label className="block text-sm text-body">
        제목 검색
        <input className={INPUT_CLASS} value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <div role="group" aria-label="문서 유형" className="flex flex-wrap items-center gap-2 lg:col-span-3 lg:row-start-2">
        {[undefined, ...types].map(type => <button key={type ?? "all"} type="button" aria-pressed={(value.contentType ?? undefined) === type}
          onClick={() => onChange({ ...value, contentType: type })}
          className={`rounded-full border border-line px-3 py-1.5 text-xs ${value.contentType === type ? "bg-strong text-on-strong" : "bg-card text-body"}`}>{type?.toUpperCase() ?? "전체"}</button>)}
      </div>
      <label className="block text-sm text-body">
        태그
        <select className={INPUT_CLASS} value={value.tag ?? ""} onChange={(event) => onChange({ ...value, tag: event.target.value || undefined })}>
          <option value="">전체</option>
          {tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
        </select>
      </label>
      <label className="block text-sm text-body">
        정렬
        <select className={INPUT_CLASS} value={value.sort ?? "updated"} onChange={(event) => onChange({ ...value, sort: event.target.value as DocumentSort })}>
          <option value="updated">최근 수정순</option>
          <option value="title">제목순</option>
        </select>
      </label>
    </div>
  );
}
