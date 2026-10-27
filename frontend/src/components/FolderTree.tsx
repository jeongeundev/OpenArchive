// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { folderChildren } from "@/lib/folders";
import type { Folder } from "@/lib/types";

/** 폴더 이름 한 칸 입력. 모달 없이 그 자리에서 입력하고, 실패하면 입력을 남긴 채 서버 문구를 보인다. */
export function FolderNameForm({ label, initial = "", submitLabel, onSubmit, onCancel }: {
  label: string;
  initial?: string;
  submitLabel: string;
  onSubmit: (name: string) => Promise<void>;
  onCancel: () => void;
}): React.ReactElement {
  const [name, setName] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await onSubmit(name.trim());
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "폴더를 저장하지 못했습니다.");
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <input aria-label={label} value={name} autoFocus onChange={event => setName(event.target.value)}
        className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm text-body" />
      <div className="flex gap-3 text-sm">
        <button type="submit" disabled={saving || !name.trim()}
          className="rounded-lg bg-strong px-3 py-1 text-on-strong hover:bg-strong/90 disabled:opacity-50">{submitLabel}</button>
        <button type="button" onClick={onCancel} className="text-muted hover:text-strong">취소</button>
      </div>
      {error !== null ? <p role="alert" className="text-sm text-failed">{error}</p> : null}
    </form>
  );
}

export function FolderTree({ folders, selectedId, granted = false, total, creating: externalCreating, onCreatingChange, onSelect, onCreate }: {
  folders: Folder[];
  selectedId: string | null;
  /** 「부여받은 문서」 보기 중인지. 그때는 「전체 문서」가 현재 위치가 아니다. */
  granted?: boolean;
  onSelect: (id: string | null) => void;
  total?: number | null;
  creating?: boolean;
  onCreatingChange?: (value: boolean) => void;
  onCreate: (name: string, parentId: string | null) => Promise<void>;
}): React.ReactElement {
  const [localCreating, setLocalCreating] = useState(false);
  const creating = externalCreating ?? localCreating;
  const setCreating = onCreatingChange ?? setLocalCreating;
  function count(folder: Folder): number { return folder.document_count + folderChildren(folders, folder.id).reduce((sum, child) => sum + count(child), 0); }
  const allSelected = selectedId === null && !granted;
  const ids = new Set(folders.map(folder => folder.id));
  const roots = folders.filter(folder => folder.parent_id === null || !ids.has(folder.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));

  function item(folder: Folder): React.ReactElement {
    const children = folderChildren(folders, folder.id);
    const selected = folder.id === selectedId;
    return (
      <li key={folder.id}>
        <button type="button" onClick={() => onSelect(folder.id)} aria-current={selected ? "true" : undefined}
          className={`flex w-full justify-between gap-2 rounded px-2 py-1 text-left text-sm ${selected ? "bg-sunken text-strong" : "text-body hover:text-strong"}`}>
          <span className="min-w-0">
            <span className="block truncate">{folder.name}</span>
          </span>
          <span className="text-muted">{count(folder)}</span>
        </button>
        {children.length > 0 ? <ul className="ml-3 border-l border-line pl-2">{children.map(item)}</ul> : null}
      </li>
    );
  }

  return (
    <nav aria-label="폴더" className="space-y-3">
      <div className="space-y-1">
        <button type="button" onClick={() => onSelect(null)} aria-current={allSelected ? "true" : undefined}
          className={`w-full rounded px-2 py-1 text-left text-sm ${allSelected ? "bg-sunken text-strong" : "text-body hover:text-strong"}`}>
          전체 문서{total != null ? <span className="float-right text-muted">{total}</span> : null}
        </button>
      </div>
      {roots.length > 0 ? <ul className="space-y-1">{roots.map(item)}</ul> : null}
      {creating ? (
        <FolderNameForm label="새 폴더 이름" submitLabel="만들기" onCancel={() => setCreating(false)}
          onSubmit={async name => { await onCreate(name, null); setCreating(false); }} />
      ) : externalCreating === undefined ? (
        <button type="button" onClick={() => setCreating(true)} className="text-sm text-accent hover:underline">새 폴더</button>
      ) : null}
    </nav>
  );
}
