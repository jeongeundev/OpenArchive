"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, reembedDocument } from "@/lib/api";
import { useDocuments } from "@/lib/useDocuments";
import { getCurrentUser } from "@/lib/user";

export function ErrorDocuments(): React.ReactElement {
  const { documents, loading, error, refresh } = useDocuments({ status: "error" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [requestingId, setRequestingId] = useState<string | null>(null);
  const anonymous = getCurrentUser() === null;

  async function reembed(id: string): Promise<void> {
    setRequestingId(id);
    setActionError(null);
    try {
      await reembedDocument(id);
      refresh();
    } catch (reason) {
      setActionError(reason instanceof ApiError ? reason.detail : reason instanceof Error ? reason.message : "재임베딩 요청에 실패했습니다.");
    } finally {
      setRequestingId(null);
    }
  }

  return (
    <section className="space-y-4" aria-labelledby="error-documents-heading">
      <h2 id="error-documents-heading" className="text-sm font-medium text-neutral-400">임베딩 실패 문서</h2>
      {(error ?? actionError) !== null && <p className="text-sm text-[#ef4444]">{actionError ?? error}</p>}
      {anonymous && <p className="text-sm text-neutral-400">사용자를 선택하면 재임베딩을 요청할 수 있습니다.</p>}
      {!loading && documents.length === 0 ? (
        <p className="rounded-lg border border-neutral-800 bg-[#141414] p-6 text-sm text-neutral-500">실패한 문서가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
          <table className="w-full text-left text-sm"><thead className="border-b border-neutral-800 text-neutral-500"><tr><th className="px-4 py-3 font-medium">제목</th><th className="px-4 py-3 font-medium">소유자</th><th className="px-4 py-3 font-medium">수정일</th><th className="px-4 py-3 font-medium"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>{documents.map((document) => <tr key={document.id} className="border-b border-neutral-800 last:border-0"><td className="px-4 py-3"><Link className="text-white hover:text-[#0ea5e9]" href={`/documents/${document.id}`}>{document.title}</Link></td><td className="px-4 py-3 text-neutral-300">{document.owner_id}</td><td className="px-4 py-3 text-neutral-400">{new Date(document.updated_at).toLocaleString("ko-KR")}</td><td className="px-4 py-3 text-right"><button type="button" disabled={anonymous || requestingId === document.id} onClick={() => void reembed(document.id)} className="rounded-lg bg-white px-3 py-2 text-xs font-medium text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-40">재임베딩</button></td></tr>)}</tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-neutral-500">권한 필터가 적용되어 현재 선택된 사용자가 볼 수 있는 문서만 표시됩니다.</p>
    </section>
  );
}
