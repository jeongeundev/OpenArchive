// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";

import { originalPreviewUrl } from "@/lib/api";
import { relationLabel } from "@/lib/relations";
import { scopeSummary } from "@/lib/scope";
import { useDocument } from "@/lib/useDocument";
import { useRelated } from "@/lib/useRelated";
import { DocumentStatusBadge } from "./StatusBadge";

const RELATED_LIMIT = 4;

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * 목록 오른쪽 정보 창 — 고른 문서의 메타와 관련 문서만 읽기 전용으로 보인다.
 * 본문은 싣지 않고(D10), 관리 동작은 상세의 「문서 관리」 한 곳에 둔다(D9).
 */
export function DocumentInfoPane({
  documentId,
  onClose,
}: {
  documentId: string;
  onClose: () => void;
}): React.ReactElement {
  const { document, loading, error } = useDocument(documentId);
  const { related } = useRelated(documentId, document?.chunk_version ?? null);

  // 머리 줄은 불러오기 전후로 같은 자리에 둔다 — 「닫기」가 다시 그려지지 않게.
  const head = (
    <div className="flex items-center gap-2">
      {document !== null ? (
        <>
          <span className="whitespace-nowrap rounded border border-line px-1.5 py-0.5 text-xs font-medium uppercase text-muted">
            {document.content_type}
          </span>
          <DocumentStatusBadge document={document} />
        </>
      ) : null}
      <button type="button" onClick={onClose}
        className="ml-auto rounded-lg border border-line px-2 py-0.5 text-xs text-body hover:bg-sunken">
        닫기
      </button>
    </div>
  );

  if (document === null) {
    return (
      <aside aria-label="문서 정보" className="space-y-5 break-words rounded-lg border border-line bg-card p-5 lg:sticky lg:top-6">
        {head}
        <p className="text-sm text-muted" role="status">
          {loading || error === null ? "불러오는 중…" : error}
        </p>
      </aside>
    );
  }

  const location = document.folder?.path.map((item) => item.name).join(" › ") || null;
  // 현재 판은 가장 큰 판 번호다. 이전 판만 준비돼 있으면 지금 원본의 미리보기가 아니다.
  const currentFile = document.files.reduce<(typeof document.files)[number] | null>(
    (latest, file) => (latest === null || file.file_version > latest.file_version ? file : latest),
    null,
  );
  const info: [string, string][] = [
    ...(location !== null ? [["위치", location] as [string, string]] : []),
    ["소유자", document.owner_id],
    ["열람 범위", scopeSummary(document)],
    ["수정", `${DATE_FORMATTER.format(new Date(document.updated_at))} · 텍스트 v${document.version}`],
    ...(document.filename !== null ? [["파일", document.filename] as [string, string]] : []),
  ];
  const relatedItems = related?.items.slice(0, RELATED_LIMIT) ?? [];

  return (
    <aside aria-label="문서 정보" className="space-y-5 break-words rounded-lg border border-line bg-card p-5 lg:sticky lg:top-6">
      {head}

      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-strong">{document.title}</h2>
        {location !== null ? <p className="text-sm text-muted">{location}</p> : null}
      </div>

      <div className="flex flex-wrap gap-2">
        <Link href={`/documents/${document.id}`}
          className="rounded-lg bg-strong px-4 py-2 text-sm font-medium text-on-strong hover:bg-strong/90">
          문서 열기
        </Link>
        {currentFile?.preview_status === "ready" ? (
          <a href={originalPreviewUrl(document.id, currentFile.file_version)} target="_blank" rel="noopener noreferrer"
            className="rounded-lg border border-line px-4 py-2 text-sm font-medium text-body hover:bg-sunken">
            원본 미리보기
          </a>
        ) : null}
      </div>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-strong">정보</h3>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {info.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="text-muted">{term}</dt>
              <dd className="text-body">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-strong">관련 문서 {relatedItems.length}</h3>
        {relatedItems.length === 0 ? (
          <p className="text-sm text-muted">관련 문서가 없습니다.</p>
        ) : (
          <ul className="space-y-2">
            {relatedItems.map((item) => (
              <li key={item.document_id} className="text-sm">
                <Link href={`/documents/${item.document_id}`} className="font-medium text-strong hover:text-accent">
                  {item.title}
                </Link>
                <p className="text-xs text-muted">
                  <span>{relationLabel(item.kind)}</span> · <span className="tabular-nums">{item.score.toFixed(3)}</span>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
