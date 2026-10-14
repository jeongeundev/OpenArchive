// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ApiError, reembedDocument } from "@/lib/api";
import { useDocuments } from "@/lib/useDocuments";

export function ErrorDocuments(): React.ReactElement {
  const { documents, loading, error, refresh } = useDocuments({ status: "error" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [requestingId, setRequestingId] = useState<string | null>(null);
  const { auth } = useAuth();
  const anonymous = !auth.authenticated;

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
      <h2 id="error-documents-heading" className="text-sm font-medium text-muted">임베딩 실패 문서</h2>
      {(error ?? actionError) !== null && <p className="text-sm text-failed">{actionError ?? error}</p>}
      {!loading && documents.length === 0 ? (
        <p className="rounded-lg border border-line bg-card p-6 text-sm text-muted">실패한 문서가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-card">
          <table className="w-full text-left text-sm"><thead className="border-b border-line text-muted"><tr><th className="px-4 py-3 font-medium">제목</th><th className="px-4 py-3 font-medium">소유자</th><th className="px-4 py-3 font-medium">수정일</th><th className="px-4 py-3 font-medium"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>{documents.map((document) => <tr key={document.id} className="border-b border-line last:border-0"><td className="px-4 py-3"><Link className="text-strong hover:text-accent" href={`/documents/${document.id}`}>{document.title}</Link></td><td className="px-4 py-3 text-body">{document.owner_id}</td><td className="px-4 py-3 text-muted">{new Date(document.updated_at).toLocaleString("ko-KR")}</td><td className="px-4 py-3 text-right">{!anonymous ? <button type="button" disabled={requestingId === document.id} onClick={() => void reembed(document.id)} className="rounded-lg bg-strong px-3 py-2 text-xs font-medium text-on-strong hover:bg-strong/90 disabled:cursor-not-allowed disabled:opacity-40">재임베딩</button> : null}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-muted">권한 필터가 적용되어 현재 선택된 사용자가 볼 수 있는 문서만 표시됩니다.</p>
    </section>
  );
}
