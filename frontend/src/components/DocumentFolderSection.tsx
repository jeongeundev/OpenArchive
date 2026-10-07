"use client";

import Link from "next/link";
import { Fragment, useState } from "react";

import { ApiError, getDocumentAccess, moveDocument } from "@/lib/api";
import { sameScope, scopeLabel } from "@/lib/folders";
import { useFolders } from "@/lib/useFolders";
import type { DocumentFolder, FolderScope } from "@/lib/types";
import { FolderSelect } from "./FolderSelect";

/**
 * 문서의 폴더 경로와 폴더 이동 (ADR-054).
 * 폴더가 null이면 아무 자리도 남기지 않는다 — 볼 수 없는 폴더의 존재가 새지 않게 (ADR-027).
 * 고른 값은 현재 폴더에서 시작한다 — 폴더가 바뀌면 부모가 key로 다시 그린다.
 * 이동은 소유자만 하며, 「폴더 범위 따름」 문서의 실효 범위가 바뀔 때만 확인을 받는다.
 * 볼 수 없게 된 폴더 안 자기 문서(hiddenFolder)는 이름 없이 그 사실만 보이고, 어디로든 옮길 수 있다.
 */
export function DocumentFolderSection({ documentId, folder, hiddenFolder = false, isOwner, disabled = false, onMoved }: {
  documentId: string;
  folder: DocumentFolder | null;
  hiddenFolder?: boolean;
  isOwner: boolean;
  disabled?: boolean;
  onMoved: () => void;
}): React.ReactElement | null {
  const { folders, refresh: refreshFolders } = useFolders(isOwner);
  const currentId = folder?.id ?? null;
  const [target, setTarget] = useState<string | null>(currentId);
  const [moving, setMoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 숨은 폴더는 고를 수 있는 값이 아니라 「폴더 없음」도 이동이다.
  const unchanged = !hiddenFolder && target === currentId;

  if (folder === null && !isOwner) return null;

  async function move(): Promise<void> {
    if (moving || unchanged) return;
    setMoving(true);
    setError(null);
    try {
      const access = await getDocumentAccess(documentId);
      const own: FolderScope = { visibility: access.visibility, users: access.users, groups: access.groups };
      const before = access.folder !== null && access.follows_folder && access.folder_scope !== null
        ? access.folder_scope : own;
      const destination = folders.find(item => item.id === target) ?? null;
      const after = destination !== null && access.follows_folder ? destination.scope : own;
      // 숨은 폴더의 범위는 알 수 없다 — 따르는 중이면 늘 바뀌는 것으로 보고 확인을 받는다.
      const hiddenBefore = access.hidden_folder && access.follows_folder;
      const beforeLabel = hiddenBefore ? "볼 수 없는 폴더의 범위" : scopeLabel(before);
      if (access.follows_folder && (hiddenBefore || !sameScope(before, after))
        && !window.confirm(`열람 범위가 바뀝니다: 「${beforeLabel}」 → 「${scopeLabel(after)}」. 옮기시겠습니까?`)) {
        return;
      }
      await moveDocument(documentId, target);
      refreshFolders();
      onMoved();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "폴더를 옮기지 못했습니다.");
    } finally {
      setMoving(false);
    }
  }

  return (
    <section className="space-y-3">
      {folder !== null ? (
        <p className="text-sm text-neutral-400">
          <span className="mr-2 text-neutral-500">폴더</span>
          {folder.path.map((item, index) => (
            <Fragment key={item.id}>
              {index > 0 ? <span className="text-neutral-600">/</span> : null}
              <Link className="text-[#0ea5e9] hover:underline" href={`/?folder=${encodeURIComponent(item.id)}`}>
                {item.name}
              </Link>
            </Fragment>
          ))}
        </p>
      ) : hiddenFolder ? (
        <p className="text-sm text-neutral-400">
          <span className="mr-2 text-neutral-500">폴더</span>
          <span>볼 수 없는 폴더</span>
        </p>
      ) : null}
      {isOwner ? (
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <FolderSelect folders={folders} value={target} onChange={setTarget}
              label="폴더" noneLabel="폴더 없음" disabled={disabled || moving} />
          </div>
          <button type="button" onClick={() => void move()}
            disabled={disabled || moving || unchanged}
            className="rounded-lg bg-white px-4 py-3 text-sm text-black hover:bg-neutral-200 disabled:opacity-50">
            옮기기
          </button>
        </div>
      ) : null}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
    </section>
  );
}
