// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import Link from "next/link";

import { scopeSummary } from "@/lib/scope";
import type { DocumentSummary } from "@/lib/types";
import { DocumentStatusBadge } from "./StatusBadge";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function DocumentTable({
  documents,
  emptyMessage = "아직 문서가 없습니다.",
}: {
  documents: DocumentSummary[];
  emptyMessage?: string;
}): React.ReactElement {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-card">
      <table className="w-full text-left text-sm text-body">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className="px-4 py-3 font-medium" scope="col">제목</th>
            <th className="px-4 py-3 font-medium" scope="col">유형</th>
            <th className="px-4 py-3 font-medium" scope="col">태그</th>
            <th className="px-4 py-3 font-medium" scope="col">소유자</th>
            <th className="px-4 py-3 font-medium" scope="col">열람 범위</th>
            <th className="px-4 py-3 font-medium" scope="col">상태</th>
            <th className="px-4 py-3 font-medium" scope="col">수정일</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {documents.length === 0 ? (
            <tr>
              <td className="px-4 py-8 text-muted" colSpan={7}>
                {emptyMessage}
              </td>
            </tr>
          ) : (
            documents.map((document) => (
              <tr key={document.id}>
                <td className="px-4 py-3 font-medium">
                  <Link
                    className="text-strong hover:text-accent"
                    href={`/documents/${document.id}`}
                  >
                    {document.title}
                  </Link>
                </td>
                <td className="px-4 py-3 uppercase text-muted">
                  {document.content_type}
                </td>
                <td className="px-4 py-3 text-muted">
                  {document.tags.length > 0 ? document.tags.join(", ") : "—"}
                </td>
                <td className="px-4 py-3 text-muted">{document.owner_id}</td>
                <td className="px-4 py-3">
                  {scopeSummary(document)}
                </td>
                <td className="px-4 py-3">
                  <DocumentStatusBadge document={document} />
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-muted">
                  {DATE_FORMATTER.format(new Date(document.updated_at))}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
