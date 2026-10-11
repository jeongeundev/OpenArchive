"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, addShareFolder, listShares, removeShareFolder } from "@/lib/api";
import type { Folder } from "@/lib/types";

interface ShareChoice {
  id: string;
  name: string;
  included: boolean;
}

/**
 * 폴더를 내 외부 공유에 넣고 뺀다. 최상위 폴더를 만든 사람(`can_share`)에게만 띄운다 — 거부는 서버 메시지로 보인다.
 * 공유 관리는 세션 전용이라 화면의 쿠키 세션으로만 부른다 (ADR-034 결정 6, #206).
 */
export function FolderShareToggles({ folder }: { folder: Folder }): React.ReactElement {
  const [shares, setShares] = useState<ShareChoice[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    // 폴더가 바뀌면 부모가 key로 새로 마운트한다.
    const controller = new AbortController();
    listShares(controller.signal)
      .then(items => {
        if (controller.signal.aborted) return;
        setShares(items.map(share => ({
          id: share.id,
          name: share.name,
          included: share.folders.some(item => item.id === folder.id),
        })));
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(reason instanceof ApiError ? reason.detail : "공유 목록을 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, [folder.id]);

  async function toggle(share: ShareChoice): Promise<void> {
    if (shares === null || pending !== null) return;
    const include = !share.included;
    const withState = (included: boolean) =>
      (current: ShareChoice[] | null) =>
        current === null ? current : current.map(item => (item.id === share.id ? { ...item, included } : item));
    setPending(share.id);
    setError(null);
    setMessage(null);
    setShares(withState(include));
    try {
      if (include) await addShareFolder(share.id, folder.id);
      else await removeShareFolder(share.id, folder.id);
      setMessage(include ? `「${share.name}」 공유에 넣었습니다.` : `「${share.name}」 공유에서 뺐습니다.`);
    } catch (reason: unknown) {
      setShares(withState(share.included));
      setError(reason instanceof ApiError ? reason.detail : "공유를 바꾸지 못했습니다.");
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="space-y-3 border-t border-neutral-800 pt-4">
      <h3 className="text-sm font-medium text-neutral-400">외부 공유</h3>
      <p className="text-sm text-neutral-500">
        공유에 넣으면 이 폴더와 하위 폴더의 「폴더 범위 따름」 문서가 소유자와 관계없이 공유 대상에게 보입니다.
        나중에 들어오는 문서도 포함됩니다.
      </p>
      <p className="text-sm text-neutral-500">「개별 지정」 문서는 빠집니다.</p>
      {loadError !== null ? (
        <p className="text-sm text-neutral-500">{loadError}</p>
      ) : shares === null ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : shares.length === 0 ? (
        <p className="text-sm text-neutral-500">
          외부 공유가 없습니다. <Link className="text-[#0ea5e9] hover:underline" href="/settings">설정 화면</Link>에서 공유를 만드세요.
        </p>
      ) : (
        <fieldset disabled={pending !== null}>
          <legend className="sr-only">폴더를 넣을 공유</legend>
          <div className="flex flex-wrap gap-4 text-sm text-neutral-300">
            {shares.map(share => (
              <label className="flex items-center gap-2" key={share.id}>
                <input checked={share.included} onChange={() => void toggle(share)} type="checkbox" />
                {share.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
      {message !== null ? <p className="text-sm text-neutral-400">{message}</p> : null}
    </section>
  );
}
