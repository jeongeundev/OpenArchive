"use client";

import { useEffect, useState } from "react";

import type { DocumentFilters as Filters, DocumentSort } from "@/lib/api";
import { SUPPORTED_CONTENT_TYPES, type ContentType } from "@/lib/types";

const INPUT_CLASS = "mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-white";

export function DocumentFilters({ value, tags, onChange }: {
  value: Filters;
  tags: string[];
  onChange: (value: Filters) => void;
}): React.ReactElement {
  const [query, setQuery] = useState(value.q ?? "");

  useEffect(() => {
    if (query === (value.q ?? "")) return;
    const timer = window.setTimeout(() => onChange({ ...value, q: query }), 300);
    return () => window.clearTimeout(timer);
  }, [query, value, onChange]);

  return (
    <div className="grid gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6 md:grid-cols-4">
      <label className="block text-sm text-neutral-300">
        제목 검색
        <input className={INPUT_CLASS} value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <label className="block text-sm text-neutral-300">
        문서 유형
        <select className={INPUT_CLASS} value={value.contentType ?? ""} onChange={(event) => onChange({ ...value, contentType: (event.target.value || undefined) as ContentType | undefined })}>
          <option value="">전체</option>
          {SUPPORTED_CONTENT_TYPES.map((type) => <option key={type} value={type}>{type.toUpperCase()}</option>)}
        </select>
      </label>
      <label className="block text-sm text-neutral-300">
        태그
        <select className={INPUT_CLASS} value={value.tag ?? ""} onChange={(event) => onChange({ ...value, tag: event.target.value || undefined })}>
          <option value="">전체</option>
          {tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
        </select>
      </label>
      <label className="block text-sm text-neutral-300">
        정렬
        <select className={INPUT_CLASS} value={value.sort ?? "updated"} onChange={(event) => onChange({ ...value, sort: event.target.value as DocumentSort })}>
          <option value="updated">최근 수정순</option>
          <option value="title">제목순</option>
        </select>
      </label>
    </div>
  );
}
