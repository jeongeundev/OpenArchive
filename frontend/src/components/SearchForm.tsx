// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useState } from "react";

import { FolderSelect } from "@/components/FolderSelect";
import { MAX_K, SUPPORTED_CONTENT_TYPES } from "@/lib/types";
import type { ContentType, Folder } from "@/lib/types";
import type { SearchInput } from "@/lib/useSearch";

export function SearchForm({
  onSearch,
  pending,
  folders = [],
  initial = null,
}: {
  onSearch: (input: SearchInput) => void;
  pending: boolean;
  folders?: Folder[];
  /** 주소에서 복원한 조건. 처음 그릴 때만 읽는다. */
  initial?: SearchInput | null;
}): React.ReactElement {
  const [query, setQuery] = useState(initial?.query ?? "");
  const [tags, setTags] = useState(initial?.tags.join(", ") ?? "");
  const [contentType, setContentType] = useState<ContentType | "">(initial?.contentType ?? "");
  const [k, setK] = useState(initial?.k ?? 10);
  const [folderId, setFolderId] = useState<string | null>(initial?.folderId ?? null);
  // 볼 수 있는 폴더가 사라졌으면(목록 갱신·권한 변경) 선택도 버린다.
  const selectedFolderId = folders.some((folder) => folder.id === folderId) ? folderId : null;

  return (
    <form
      className="space-y-4 rounded-lg border border-line bg-card p-6"
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
      <label className="block text-sm text-body">
        검색어
        <input
          required
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-strong"
        />
      </label>

      <div className="grid gap-4 md:grid-cols-3">
        <label className="block text-sm text-body">
          태그 (쉼표로 구분)
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-strong"
          />
        </label>
        <label className="block text-sm text-body">
          문서 유형
          <select
            value={contentType}
            onChange={(event) => setContentType(event.target.value as ContentType | "")}
            className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-strong"
          >
            <option value="">전체</option>
            {SUPPORTED_CONTENT_TYPES.map((type) => (
              <option key={type} value={type}>{type.toUpperCase()}</option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-body">
          결과 수
          <input
            type="number"
            min={1}
            max={MAX_K}
            value={k}
            onChange={(event) => setK(Math.min(MAX_K, Math.max(1, Number(event.target.value))))}
            className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-strong"
          />
        </label>
      </div>

      {folders.length > 0 ? (
        <div className="space-y-2">
          <FolderSelect
            folders={folders}
            value={selectedFolderId}
            onChange={setFolderId}
            label="폴더"
            noneLabel="전체 폴더"
          />
          <p className="text-xs text-muted">
            폴더는 직접 맞은 결과에만 걸립니다(하위 폴더 포함). 관계로 이어진 결과는 폴더 밖 문서일 수 있습니다.
          </p>
        </div>
      ) : null}

      <button
        type="submit"
        disabled={pending || !query.trim()}
        className="rounded-lg bg-strong px-4 py-2 text-sm font-medium text-on-strong hover:bg-strong/90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "검색 중…" : "검색"}
      </button>
    </form>
  );
}
