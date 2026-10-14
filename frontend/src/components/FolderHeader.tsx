// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useState } from "react";

import { ApiError, createFolder, deleteFolder, renameFolder, transferFolderOwner } from "@/lib/api";
import { scopeLabel } from "@/lib/folders";
import type { Folder } from "@/lib/types";
import { OwnerTransferForm } from "./OwnerTransfer";
import { FolderAccessPanel } from "./FolderAccessPanel";
import { FolderShareToggles } from "./FolderShareToggles";
import { FolderNameForm } from "./FolderTree";

function folderPath(folder: Folder, folders: Folder[]): string {
  const byId = new Map(folders.map(item => [item.id, item]));
  const names = [folder.name];
  const seen = new Set([folder.id]);
  let parent = folder.parent_id === null ? undefined : byId.get(folder.parent_id);
  while (parent !== undefined && !seen.has(parent.id)) {
    seen.add(parent.id);
    names.unshift(parent.name);
    parent = parent.parent_id === null ? undefined : byId.get(parent.parent_id);
  }
  return names.join("/");
}

/**
 * 고른 폴더의 경로·범위와 조작. 권한이 없어도 버튼을 숨기지 않는다 — 거부는 서버가 보인다(B10).
 * 최상위 폴더는 「열람 범위」로 범위 패널을 펼친다. 하위 폴더는 범위를 갖지 않는다.
 */
export function FolderHeader({ folder, folders, onChanged, onDeleted, onAccessSaved }: {
  folder: Folder;
  folders: Folder[];
  onChanged: () => void;
  onDeleted: () => void;
  onAccessSaved?: () => void;
}): React.ReactElement {
  const [mode, setMode] = useState<"idle" | "child" | "rename" | "owner">("idle");
  const [accessOpen, setAccessOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = folder.parent_id === null ? scopeLabel(folder.scope) : `상위 폴더 범위 따름(${scopeLabel(folder.scope)})`;

  async function remove() {
    if (!window.confirm(`「${folder.name}」 폴더를 삭제하시겠습니까? 빈 폴더만 삭제할 수 있습니다.`)) return;
    setError(null);
    try {
      await deleteFolder(folder.id);
      onDeleted();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "폴더를 삭제하지 못했습니다.");
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <h2 className="text-lg font-semibold text-strong">{folderPath(folder, folders)}</h2>
      <p className="text-sm text-muted">
        <span>열람 범위</span> · <span className="text-body">{label}</span>
      </p>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" onClick={() => { setMode("child"); setError(null); }} className="text-muted hover:text-strong">하위 폴더</button>
        <button type="button" onClick={() => { setMode("rename"); setError(null); }} className="text-muted hover:text-strong">이름 변경</button>
        <button type="button" onClick={remove} className="text-muted hover:text-strong">삭제</button>
        <button type="button" onClick={() => { setMode(mode === "owner" ? "idle" : "owner"); setError(null); }} className="text-muted hover:text-strong">소유자 이전</button>
        {folder.parent_id === null ? (
          <button type="button" aria-expanded={accessOpen} onClick={() => setAccessOpen(open => !open)}
            className="text-muted hover:text-strong">열람 범위</button>
        ) : null}
        {folder.can_share ? (
          <button type="button" aria-expanded={shareOpen} onClick={() => setShareOpen(open => !open)}
            className="text-muted hover:text-strong">외부 공유</button>
        ) : null}
        {!folder.can_manage ? <span className="text-muted">폴더를 만든 사람만 바꿀 수 있습니다</span> : null}
      </div>
      {mode === "child" ? (
        <FolderNameForm key="child" label="하위 폴더 이름" submitLabel="만들기" onCancel={() => setMode("idle")}
          onSubmit={async name => { await createFolder({ name, parentId: folder.id }); setMode("idle"); onChanged(); }} />
      ) : mode === "rename" ? (
        <FolderNameForm key="rename" label="폴더 이름" initial={folder.name} submitLabel="저장" onCancel={() => setMode("idle")}
          onSubmit={async name => { await renameFolder(folder.id, name); setMode("idle"); onChanged(); }} />
      ) : null}
      {mode === "owner" ? <OwnerTransferForm key={folder.id + folder.created_by} owner={folder.created_by} kind="folder" submitLabel="이전" onTransfer={async owner => {
        const result = await transferFolderOwner(folder.id, owner);
        setMode("idle");
        if (result.still_visible) onChanged(); else onDeleted();
      }} /> : null}
      {error !== null ? <p role="alert" className="text-sm text-failed">{error}</p> : null}
      {accessOpen && folder.parent_id === null ? (
        <FolderAccessPanel folder={folder} onSaved={onAccessSaved ?? onChanged} />
      ) : null}
      {shareOpen && folder.can_share ? <FolderShareToggles key={folder.id} folder={folder} /> : null}
    </div>
  );
}
