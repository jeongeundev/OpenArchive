// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { scopeSummary } from "@/lib/scope";
import type { DocumentDetail } from "@/lib/types";
import { joinMeta, OwnerLabel } from "./OwnerLabel";
import { DocumentStatusBadge } from "./StatusBadge";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/**
 * 문서 상세 머리글 — 제목 옆 형식 칩·상태 배지, 아래 메타 한 줄(위치 · 소유자 · 열람 범위 칩 · 수정 일시).
 * 볼 수 없는 폴더 안 문서는 위치 칸을 통째로 뺀다 — 자리 표시도 폴더의 존재를 드러낸다.
 */
export function DocumentHeader({
  document,
}: {
  document: DocumentDetail;
}): React.ReactElement {
  const meta = joinMeta([
    document.folder?.path.map((item) => item.name).join(" › "),
    <OwnerLabel key="owner" owner={document.owner_id} department={document.owner_department} />,
    <span key="scope" data-testid="document-scope" className="rounded bg-sunken px-1.5 py-0.5 text-xs text-body">
      {scopeSummary(document)}
    </span>,
    formatDate(document.updated_at),
  ]);

  return (
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 break-words text-3xl font-bold text-strong">{document.title}</h1>
        <span data-testid="document-format" className="shrink-0 rounded bg-accent-soft px-1.5 py-0.5 text-xs font-medium text-accent">
          {document.content_type.toUpperCase()}
        </span>
        <DocumentStatusBadge document={document} />
      </div>
      <p className="break-words text-sm text-muted" data-testid="document-header-meta">
        {meta}
      </p>
    </header>
  );
}
