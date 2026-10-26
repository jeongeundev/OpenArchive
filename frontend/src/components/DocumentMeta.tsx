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

function indexStatus(document: DocumentDetail): string {
  if (document.chunk_count === 0) return "아직 색인된 청크가 없습니다.";
  if (document.chunk_version === document.version) {
    return `청크 ${document.chunk_count}개 · 현재 버전(v${document.version}) 기준`;
  }
  return `청크 ${document.chunk_count}개 · v${document.chunk_version} 기준 — 재임베딩 중입니다`;
}

export function DocumentMeta({
  document,
}: {
  document: DocumentDetail;
}): React.ReactElement {
  return (
    <section className="rounded-lg border border-line bg-card p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-4xl font-semibold text-strong">{document.title}</h1>
        <DocumentStatusBadge document={document} />
      </div>

      <dl className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted">파일명</dt>
          <dd className="mt-1 text-body">{document.filename ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-muted">유형</dt>
          <dd className="mt-1 uppercase text-body">{document.content_type}</dd>
        </div>
        <div>
          <dt className="text-muted">열람 범위</dt>
          <dd className="mt-1 text-body">
            {scopeSummary(document)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">소유자</dt>
          <dd className="mt-1 text-body">{document.owner_id}</dd>
        </div>
        <div>
          <dt className="text-muted">생성 일시</dt>
          <dd className="mt-1 text-body">{formatDate(document.created_at)}</dd>
        </div>
        <div>
          <dt className="text-muted">수정 일시</dt>
          <dd className="mt-1 text-body">{formatDate(document.updated_at)}</dd>
        </div>
      </dl>

      <p className="mt-6 border-t border-line pt-4 text-sm text-muted">
        {indexStatus(document)}
      </p>
    </section>
  );
}
