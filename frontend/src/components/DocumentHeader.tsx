// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { scopeSummary } from "@/lib/scope";
import type { DocumentDetail } from "@/lib/types";
import { DocumentStatusBadge } from "./StatusBadge";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/**
 * 문서 상세 머리글 — 제목·상태 배지와 메타 한 줄(위치 · 형식 · 소유자 · 열람 범위 · 수정 일시).
 * 볼 수 없는 폴더 안 문서는 위치 칸을 통째로 뺀다 — 자리 표시도 폴더의 존재를 드러낸다.
 */
export function DocumentHeader({
  document,
}: {
  document: DocumentDetail;
}): React.ReactElement {
  const meta = [
    document.folder?.path.map((item) => item.name).join(" › "),
    document.content_type.toUpperCase(),
    document.owner_id,
    scopeSummary(document),
    formatDate(document.updated_at),
  ].filter((item): item is string => item !== undefined && item !== "");

  return (
    <header className="space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="break-words text-3xl font-semibold text-strong">{document.title}</h1>
        <DocumentStatusBadge document={document} />
      </div>
      <p className="break-words text-sm text-muted" data-testid="document-header-meta">
        {meta.join(" · ")}
      </p>
    </header>
  );
}
