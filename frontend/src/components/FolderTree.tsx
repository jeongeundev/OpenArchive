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

const FOLDER_ICON = "M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z";

/** 트리 한 줄 — 폴더 아이콘(테두리만)·이름·수. 선택 줄은 강조색 칸이다. */
function TreeRow({ label, count, selected, onClick }: {
  label: string;
  count?: number | null;
  selected: boolean;
  onClick: () => void;
}): React.ReactElement {
  return (
    <button type="button" onClick={onClick} aria-current={selected ? "true" : undefined}
      className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${selected ? "bg-accent-soft font-semibold text-accent" : "text-body hover:text-strong"}`}>
      <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d={FOLDER_ICON} /></svg>
      <span className="min-w-0 flex-1 break-words">{label}</span>
      {count != null ? <span className={`shrink-0 text-xs ${selected ? "" : "text-muted"}`}>{count}</span> : null}
    </button>
  );
}

export function FolderTree({ folders, selectedId, granted = false, total, unfiledCount, unfiledSelected = false, creating: externalCreating, onCreatingChange, onSelect, onSelectUnfiled, onCreate }: {
  folders: Folder[];
  selectedId: string | null;
  /** 「부여받은 문서」·「내 문서」 보기 중인지. 그때는 「전체 문서」가 현재 위치가 아니다. */
  granted?: boolean;
  onSelect: (id: string | null) => void;
  total?: number | null;
  /** 폴더 미지정 문서 수. 0이거나 모르면 「폴더 미지정」 줄을 숨긴다. */
  unfiledCount?: number | null;
  unfiledSelected?: boolean;
  onSelectUnfiled?: () => void;
  creating?: boolean;
  onCreatingChange?: (value: boolean) => void;
  onCreate: (name: string, parentId: string | null) => Promise<void>;
}): React.ReactElement {
  const [localCreating, setLocalCreating] = useState(false);
  const creating = externalCreating ?? localCreating;
  const setCreating = onCreatingChange ?? setLocalCreating;
  function count(folder: Folder): number { return folder.document_count + folderChildren(folders, folder.id).reduce((sum, child) => sum + count(child), 0); }
  const allSelected = selectedId === null && !granted && !unfiledSelected;
  const ids = new Set(folders.map(folder => folder.id));
  const roots = folders.filter(folder => folder.parent_id === null || !ids.has(folder.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));

  function item(folder: Folder): React.ReactElement {
    const children = folderChildren(folders, folder.id);
    return (
      <li key={folder.id}>
        <TreeRow label={folder.name} count={count(folder)} selected={folder.id === selectedId} onClick={() => onSelect(folder.id)} />
        {children.length > 0 ? <ul className="mt-1 ml-4 space-y-1">{children.map(item)}</ul> : null}
      </li>
    );
  }

  return (
    <nav aria-label="폴더" className="space-y-1">
      <TreeRow label="전체 문서" count={total} selected={allSelected} onClick={() => onSelect(null)} />
      {roots.length > 0 ? <ul className="space-y-1">{roots.map(item)}</ul> : null}
      {unfiledCount && onSelectUnfiled ? <TreeRow label="폴더 미지정" count={unfiledCount} selected={unfiledSelected} onClick={onSelectUnfiled} /> : null}
      {creating ? (
        <div className="pt-2"><FolderNameForm label="새 폴더 이름" submitLabel="만들기" onCancel={() => setCreating(false)}
          onSubmit={async name => { await onCreate(name, null); setCreating(false); }} /></div>
      ) : externalCreating === undefined ? (
        <button type="button" onClick={() => setCreating(true)} className="px-3 pt-2 text-sm text-accent hover:underline">새 폴더</button>
      ) : null}
    </nav>
  );
}
