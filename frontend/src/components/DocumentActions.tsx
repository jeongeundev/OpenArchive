"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, deleteDocument, reembedDocument } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

export function DocumentActions({
  document,
  disabled,
  onChanged,
}: {
  document: DocumentSummary;
  disabled: boolean;
  onChanged: () => void;
}): React.ReactElement {
  const router = useRouter();
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove(): Promise<void> {
    if (!window.confirm("문서를 삭제하시겠습니까? 청크와 벡터도 함께 삭제됩니다.")) return;

    setWorking(true);
    setError(null);
    try {
      await deleteDocument(document.id);
      router.push("/");
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "문서를 삭제하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function reembed(): Promise<void> {
    setWorking(true);
    setError(null);
    try {
      await reembedDocument(document.id);
      onChanged();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "재임베딩을 요청하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  const actionsDisabled = disabled || working;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        {document.embedding_status === "error" ? (
          <button
            className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
            disabled={actionsDisabled}
            onClick={() => void reembed()}
            type="button"
          >
            재임베딩
          </button>
        ) : null}
        <button
          className="text-sm text-neutral-500 hover:text-neutral-300 disabled:cursor-not-allowed disabled:text-neutral-600"
          disabled={actionsDisabled}
          onClick={() => void remove()}
          type="button"
        >
          삭제
        </button>
      </div>
      {error !== null ? <p className="text-sm text-[#ef4444]">{error}</p> : null}
    </div>
  );
}
