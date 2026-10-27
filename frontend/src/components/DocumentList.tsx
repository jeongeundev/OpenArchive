// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import type { DocumentSummary } from "@/lib/types";
import { DocumentStatusBadge } from "./StatusBadge";

function dateLabel(value: string): string {
  const date = new Date(value);
  const pad = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function DocumentList({ documents, emptyMessage = "아직 문서가 없습니다." }: {
  documents: DocumentSummary[];
  emptyMessage?: string;
}): React.ReactElement {
  if (!documents.length) return <p className="rounded-xl border border-line bg-card p-6 text-sm text-muted">{emptyMessage}</p>;
  const cell = "block min-w-0 break-words px-4 py-2 lg:table-cell lg:py-4";
  return (
    <table className="block w-full table-fixed rounded-xl border border-line bg-card text-left text-sm lg:table">
      <thead className="hidden bg-sunken text-xs text-muted lg:table-header-group">
        <tr>{["이름", "위치", "소유자", "열람 범위", "수정일", "상태"].map((label, index) => <th key={label} className={`px-4 py-3 font-medium ${index === 0 ? "w-1/3" : ""}`}>{label}</th>)}</tr>
      </thead>
      <tbody className="block lg:table-row-group">
        {documents.map(document => {
          const department = document.owner_department;
          const isPublic = document.effective_visibility === "public";
          return <tr key={document.id} className="block border-t border-line py-3 lg:table-row lg:py-0">
            <td className={cell}>
              <div className="flex items-start gap-2">
                <span className="shrink-0 whitespace-nowrap rounded bg-accent-soft px-1.5 py-0.5 text-xs font-medium uppercase text-accent">{document.content_type}</span>
                <Link href={`/documents/${document.id}`} className="min-w-0 break-words font-semibold text-strong hover:text-accent">{document.title}</Link>
              </div>
              {document.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted">{document.tags.map(tag => <span key={tag}>#{tag}</span>)}</div>}
            </td>
            <td className={`${cell} text-muted`}>{document.folder_path?.join(" › ")}</td>
            <td className={`${cell} text-body`}>{document.owner_id}{department && <div className="mt-1 text-xs text-muted">{department.folder_id ? <Link href={`/documents?folder=${encodeURIComponent(department.folder_id)}`} className="hover:text-accent">{department.name}</Link> : department.name}</div>}</td>
            <td className={cell}><span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs ${isPublic ? "bg-done-soft text-done" : "bg-sunken text-body"}`}>{isPublic ? "조직 공개" : "제한"}</span>{document.follows_folder && <div className="mt-1 text-xs text-muted">폴더 범위 따름</div>}</td>
            <td className={`${cell} text-xs text-muted`}><time dateTime={document.updated_at}>{dateLabel(document.updated_at)}</time></td>
            <td className={cell}><DocumentStatusBadge document={document} /></td>
          </tr>;
        })}
      </tbody>
    </table>
  );
}
