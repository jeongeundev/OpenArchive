// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { DocumentDetail } from "@/lib/types";

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

/** 「문서 정보」 상자 — 제목·배지·형식·소유자·열람 범위는 머리글(DocumentHeader)이 맡는다. */
export function DocumentMeta({
  document,
}: {
  document: DocumentDetail;
}): React.ReactElement {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
      <h2 className="text-sm font-semibold text-strong">문서 정보</h2>

      <dl className="mt-3 space-y-3 text-sm">
        <div>
          <dt className="text-muted">파일명</dt>
          <dd className="mt-1 break-words text-body">{document.filename ?? "—"}</dd>
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

      <p className="mt-4 border-t border-line pt-3 text-sm text-muted">
        {indexStatus(document)}
      </p>
    </section>
  );
}
