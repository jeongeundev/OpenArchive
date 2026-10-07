"use client";

import { useState } from "react";

import { ApiError, createFolder, deleteFolder, renameFolder } from "@/lib/api";
import { scopeLabel } from "@/lib/folders";
import type { Folder } from "@/lib/types";
import { FolderAccessPanel } from "./FolderAccessPanel";
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
  const [mode, setMode] = useState<"idle" | "child" | "rename">("idle");
  const [accessOpen, setAccessOpen] = useState(false);
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
    <div className="space-y-3 rounded-lg border border-neutral-800 bg-[#141414] p-4">
      <h2 className="text-lg font-semibold text-white">{folderPath(folder, folders)}</h2>
      <p className="text-sm text-neutral-400">
        <span>열람 범위</span> · <span className="text-neutral-300">{label}</span>
      </p>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" onClick={() => { setMode("child"); setError(null); }} className="text-neutral-500 hover:text-neutral-300">하위 폴더</button>
        <button type="button" onClick={() => { setMode("rename"); setError(null); }} className="text-neutral-500 hover:text-neutral-300">이름 변경</button>
        <button type="button" onClick={remove} className="text-neutral-500 hover:text-neutral-300">삭제</button>
        {folder.parent_id === null ? (
          <button type="button" aria-expanded={accessOpen} onClick={() => setAccessOpen(open => !open)}
            className="text-neutral-500 hover:text-neutral-300">열람 범위</button>
        ) : null}
        {!folder.can_manage ? <span className="text-neutral-500">폴더를 만든 사람만 바꿀 수 있습니다</span> : null}
      </div>
      {mode === "child" ? (
        <FolderNameForm key="child" label="하위 폴더 이름" submitLabel="만들기" onCancel={() => setMode("idle")}
          onSubmit={async name => { await createFolder({ name, parentId: folder.id }); setMode("idle"); onChanged(); }} />
      ) : mode === "rename" ? (
        <FolderNameForm key="rename" label="폴더 이름" initial={folder.name} submitLabel="저장" onCancel={() => setMode("idle")}
          onSubmit={async name => { await renameFolder(folder.id, name); setMode("idle"); onChanged(); }} />
      ) : null}
      {error !== null ? <p role="alert" className="text-sm text-[#ef4444]">{error}</p> : null}
      {accessOpen && folder.parent_id === null ? (
        <FolderAccessPanel folder={folder} onSaved={onAccessSaved ?? onChanged} />
      ) : null}
    </div>
  );
}
