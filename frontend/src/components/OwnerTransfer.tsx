"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, listPrincipals, transferDocumentOwner } from "@/lib/api";

export function OwnerTransferForm({ owner, submitLabel, disabled = false, kind = "document", onTransfer }: {
  owner: string;
  submitLabel: string;
  disabled?: boolean;
  kind?: "document" | "folder";
  onTransfer: (owner: string) => Promise<void>;
}): React.ReactElement {
  const [users, setUsers] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    listPrincipals(controller.signal).then(principals => {
      if (!controller.signal.aborted) setUsers(principals.users);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.detail : "사용자 목록을 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selected || working || disabled) return;
    if (!window.confirm(`소유자를 ${selected} 사용자에게 이전하시겠습니까? 이전하면 열람 범위대로만 봅니다. ${kind === "document" ? "이전 소유자의 외부 공유에서 이 문서는 빠집니다." : "하위 폴더와 문서의 소유자는 바뀌지 않습니다."}`)) return;
    setWorking(true);
    setError(null);
    try { await onTransfer(selected); }
    catch (reason: unknown) { setError(reason instanceof ApiError ? reason.detail : "소유자를 이전하지 못했습니다."); }
    finally { setWorking(false); }
  }

  return <form className="space-y-3" onSubmit={event => void submit(event)}>
    <p className="text-sm text-neutral-300">현재 소유자: {owner}</p>
    <label className="block text-sm text-neutral-400">이전받을 사용자
      <select className="mt-2 block rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" value={selected} disabled={disabled || working} onChange={event => setSelected(event.target.value)}>
        <option value="">사용자를 고르세요</option>
        {users.filter(user => user !== owner).map(user => <option key={user} value={user}>{user}</option>)}
      </select>
    </label>
    <button type="submit" disabled={disabled || working || !selected} className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400">{submitLabel}</button>
    {error !== null ? <p role="alert" className="text-sm text-[#ef4444]">{error}</p> : null}
  </form>;
}

export function OwnerTransfer({ documentId, owner, disabled, onTransferred }: {
  documentId: string;
  owner: string;
  disabled?: boolean;
  onTransferred: () => void;
}): React.ReactElement {
  const router = useRouter();
  return <section className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6">
    <h2 className="text-sm font-medium text-neutral-400">소유자</h2>
    <OwnerTransferForm key={owner} owner={owner} disabled={disabled} submitLabel="소유자 이전" onTransfer={async next => {
      const result = await transferDocumentOwner(documentId, next);
      if (result.still_visible) onTransferred();
      else router.push("/");
    }} />
  </section>;
}
