// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import type { DocumentFilters as Filters, DocumentSort } from "@/lib/api";
import { SUPPORTED_CONTENT_TYPES, type ContentType } from "@/lib/types";

const INPUT_CLASS = "mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-strong";

export function DocumentFilters({ value, tags, onChange }: {
  value: Filters;
  tags: string[];
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
    <div className="grid gap-4 rounded-lg border border-line bg-card p-6 md:grid-cols-4">
      <label className="block text-sm text-body">
        제목 검색
        <input className={INPUT_CLASS} value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <label className="block text-sm text-body">
        문서 유형
        <select className={INPUT_CLASS} value={value.contentType ?? ""} onChange={(event) => onChange({ ...value, contentType: (event.target.value || undefined) as ContentType | undefined })}>
          <option value="">전체</option>
          {SUPPORTED_CONTENT_TYPES.map((type) => <option key={type} value={type}>{type.toUpperCase()}</option>)}
        </select>
      </label>
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
