// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useId, useState } from "react";

import { FolderSelect } from "@/components/FolderSelect";
import { MAX_K, SUPPORTED_CONTENT_TYPES } from "@/lib/types";
import type { ContentType, Folder } from "@/lib/types";
import type { SearchInput } from "@/lib/useSearch";

export function SearchForm({
  onSearch,
  pending,
  folders = [],
  initial = null,
  children = null,
}: {
  onSearch: (input: SearchInput) => void;
  pending: boolean;
  folders?: Folder[];
  /** 주소에서 복원한 조건. 처음 그릴 때만 읽는다. */
  initial?: SearchInput | null;
  /** 필터 열 오른쪽 단에 놓을 결과(답변·검색 결과). */
  children?: React.ReactNode;
}): React.ReactElement {
  const queryId = useId();
  const [query, setQuery] = useState(initial?.query ?? "");
  const [tags, setTags] = useState(initial?.tags.join(", ") ?? "");
  const [contentType, setContentType] = useState<ContentType | "">(initial?.contentType ?? "");
  const [k, setK] = useState(initial?.k ?? 10);
  const [folderId, setFolderId] = useState<string | null>(initial?.folderId ?? null);
  // 볼 수 있는 폴더가 사라졌으면(목록 갱신·권한 변경) 선택도 버린다.
  const selectedFolderId = folders.some((folder) => folder.id === folderId) ? folderId : null;

  return (
    <form
      className="space-y-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (!query.trim()) return;
        onSearch({
          query: query.trim(),
          tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
          contentType: contentType || null,
          folderId: selectedFolderId,
          k: Math.min(MAX_K, Math.max(1, k)),
        });
      }}
    >
      <div data-testid="search-query-row" className="flex items-stretch gap-3">
        <div className="relative min-w-0 flex-1">
          <label htmlFor={queryId} className="sr-only">검색어</label>
          <span aria-hidden="true" className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-accent">✦</span>
          <input
            id={queryId}
            required
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-14 w-full rounded-xl border border-accent bg-card pl-11 pr-4 text-strong"
          />
        </div>
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="shrink-0 rounded-xl bg-accent px-6 text-sm font-semibold text-on-strong hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "검색 중…" : "검색"}
        </button>
      </div>

      <div data-testid="search-columns" className="grid items-start gap-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <div className="min-w-0 space-y-2">
          <div data-testid="search-filter-row" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
            {folders.length > 0 ? (
              <FolderSelect
                folders={folders}
                value={selectedFolderId}
                onChange={setFolderId}
                label="폴더"
                noneLabel="전체 폴더"
              />
            ) : null}
            <label className="block text-sm text-muted">
              문서 유형
              <select
                value={contentType}
                onChange={(event) => setContentType(event.target.value as ContentType | "")}
                className="mt-2 w-full rounded-lg border border-line bg-card px-3 py-2 text-sm text-strong"
              >
                <option value="">전체</option>
                {SUPPORTED_CONTENT_TYPES.map((type) => (
                  <option key={type} value={type}>{type.toUpperCase()}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm text-muted">
              태그 (쉼표로 구분)
              <input
                value={tags}
                onChange={(event) => setTags(event.target.value)}
                className="mt-2 w-full rounded-lg border border-line bg-card px-3 py-2 text-sm text-strong"
              />
            </label>
            <label className="block text-sm text-muted">
              결과 수
              <input
                type="number"
                min={1}
                max={MAX_K}
                value={k}
                onChange={(event) => setK(Math.min(MAX_K, Math.max(1, Number(event.target.value))))}
                className="mt-2 w-full rounded-lg border border-line bg-card px-3 py-2 text-sm text-strong"
              />
            </label>
          </div>
          {folders.length > 0 ? (
            <p className="text-xs text-muted">
              폴더는 직접 맞은 결과에만 걸립니다(하위 폴더 포함). 관계로 이어진 결과는 폴더 밖 문서일 수 있습니다.
            </p>
          ) : null}
        </div>
        <div className="min-w-0">{children}</div>
      </div>
    </form>
  );
}
