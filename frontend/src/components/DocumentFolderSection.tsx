// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";

import { ApiError, getFolderSuggestion } from "@/lib/api";
import { moveWithScopeConfirm } from "@/lib/folders";
import { useFolders } from "@/lib/useFolders";
import type { DocumentFolder, FolderSuggestion } from "@/lib/types";
import { FolderSelect } from "./FolderSelect";

/**
 * 문서의 폴더 경로와 폴더 이동 (ADR-054).
 * 폴더가 null이면 아무 자리도 남기지 않는다 — 볼 수 없는 폴더의 존재가 새지 않게 (ADR-027).
 * 고른 값은 현재 폴더에서 시작한다 — 폴더가 바뀌면 부모가 key로 다시 그린다.
 * 이동은 소유자만 하며, 「폴더 범위 따름」 문서의 실효 범위가 바뀔 때만 확인을 받는다.
 * 볼 수 없게 된 폴더 안 자기 문서(hiddenFolder)는 이름 없이 그 사실만 보이고, 어디로든 옮길 수 있다.
 * 소유자에게는 폴더에 든 관계 이웃의 절반 이상이 모인 폴더를 추천한다(ADR-064) — 고르면 선택칸만 바뀌고 옮기기는 사람이 누른다.
 */
export function DocumentFolderSection({ documentId, folder, hiddenFolder = false, isOwner, disabled = false, onMoved }: {
  documentId: string;
  folder: DocumentFolder | null;
  hiddenFolder?: boolean;
  isOwner: boolean;
  disabled?: boolean;
  onMoved: () => void;
}): React.ReactElement | null {
  const { folders, loading: foldersLoading, refresh: refreshFolders } = useFolders(isOwner);
  const currentId = folder?.id ?? null;
  const [target, setTarget] = useState<string | null>(currentId);
  const [moving, setMoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<FolderSuggestion | null>(null);
  // 숨은 폴더는 고를 수 있는 값이 아니라 「폴더 없음」도 이동이다.
  const unchanged = !hiddenFolder && target === currentId;

  useEffect(() => {
    if (!isOwner) return;
    const controller = new AbortController();
    // 추천은 곁들이는 정보다 — 실패하면 자리 없이 넘어간다.
    getFolderSuggestion(documentId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setSuggestion(result.suggestion ?? null); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [documentId, isOwner]);

  if (folder === null && !isOwner) return null;
  const suggestionLabel = suggestion?.path.map(item => item.name).join("/");

  async function move(): Promise<void> {
    if (moving || unchanged) return;
    setMoving(true);
    setError(null);
    try {
      if (!await moveWithScopeConfirm(documentId, target, folders)) return;
      refreshFolders();
      onMoved();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "폴더를 옮기지 못했습니다.");
    } finally {
      setMoving(false);
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-5">
      <h2 className="text-sm font-semibold text-strong">폴더</h2>
      {folder !== null ? (
        <p className="text-sm text-muted">
          {folder.path.map((item, index) => (
            <Fragment key={item.id}>
              {index > 0 ? <span className="text-faint">/</span> : null}
              <Link className="text-accent hover:underline" href={`/documents?folder=${encodeURIComponent(item.id)}`}>
                {item.name}
              </Link>
            </Fragment>
          ))}
        </p>
      ) : hiddenFolder ? (
        <p className="text-sm text-muted">
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
            className="rounded-lg bg-strong px-4 py-3 text-sm text-on-strong hover:bg-strong/90 disabled:opacity-50">
            옮기기
          </button>
        </div>
      ) : null}
      {isOwner && suggestion !== null ? (
        <p className="text-sm text-muted">
          <span className="mr-2 text-muted">추천 폴더</span>
          <button type="button" aria-label={`추천 폴더 ${suggestionLabel} 고르기`}
            onClick={() => setTarget(suggestion.folder_id)} disabled={disabled || moving || foldersLoading}
            className="text-accent hover:underline disabled:text-faint">
            {suggestionLabel}
          </button>
          <span className="ml-2 text-muted">
            {`폴더에 있는 관련 문서 ${suggestion.total}개 중 ${suggestion.votes}개가 이 폴더에 있습니다.`}
          </span>
        </p>
      ) : null}
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
    </section>
  );
}
