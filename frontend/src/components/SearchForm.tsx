"use client";

import { useState } from "react";

import { MAX_K, SUPPORTED_CONTENT_TYPES } from "@/lib/types";
import type { ContentType } from "@/lib/types";
import type { SearchInput } from "@/lib/useSearch";

export function SearchForm({
  onSearch,
  pending,
}: {
  onSearch: (input: SearchInput) => void;
  pending: boolean;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState("");
  const [contentType, setContentType] = useState<ContentType | "">("");
  const [k, setK] = useState(10);

  return (
    <form
      className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (!query.trim()) return;
        onSearch({
          query: query.trim(),
          tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
          contentType: contentType || null,
          k: Math.min(MAX_K, Math.max(1, k)),
        });
      }}
    >
      <label className="block text-sm text-neutral-300">
        검색어
        <input
          required
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-white"
        />
      </label>

      <div className="grid gap-4 md:grid-cols-3">
        <label className="block text-sm text-neutral-300">
          태그 (쉼표로 구분)
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-white"
          />
        </label>
        <label className="block text-sm text-neutral-300">
          문서 유형
          <select
            value={contentType}
            onChange={(event) => setContentType(event.target.value as ContentType | "")}
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-white"
          >
            <option value="">전체</option>
            {SUPPORTED_CONTENT_TYPES.map((type) => (
              <option key={type} value={type}>{type.toUpperCase()}</option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-neutral-300">
          결과 수
          <input
            type="number"
            min={1}
            max={MAX_K}
            value={k}
            onChange={(event) => setK(Math.min(MAX_K, Math.max(1, Number(event.target.value))))}
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-white"
          />
        </label>
      </div>

      <button
        type="submit"
        disabled={pending || !query.trim()}
        className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "검색 중…" : "검색"}
      </button>
    </form>
  );
}
