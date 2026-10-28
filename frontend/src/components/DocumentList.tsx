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

/** 유형 배지 색 — 의미 토큰만 쓴다. PDF는 붉은 계열, 표 계산 문서는 초록 계열, 나머지는 강조색. */
function typeBadgeClass(type: string): string {
  if (type === "pdf") return "bg-failed-soft text-failed";
  if (type === "xlsx" || type === "csv") return "bg-done-soft text-done";
  return "bg-accent-soft text-accent";
}

export function DocumentList({ documents, emptyMessage = "아직 문서가 없습니다." }: {
  documents: DocumentSummary[];
  emptyMessage?: string;
}): React.ReactElement {
  if (!documents.length) return <p className="rounded-xl border border-line bg-card p-6 text-sm text-muted">{emptyMessage}</p>;
  const cell = "block min-w-0 break-words px-4 py-2 align-top lg:table-cell lg:py-4";
  return (
    <table className="block w-full table-fixed overflow-hidden rounded-xl border border-line bg-card text-left text-sm lg:table">
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
                <span className={`mt-0.5 shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-bold uppercase ${typeBadgeClass(document.content_type)}`}>{document.content_type}</span>
                <Link href={`/documents/${document.id}`} className="min-w-0 break-words font-semibold text-strong hover:text-accent">{document.title}</Link>
              </div>
              {document.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted">{document.tags.map(tag => <span key={tag}>#{tag}</span>)}</div>}
            </td>
            <td className={`${cell} text-muted`}>{document.folder_path?.length ? document.folder_path.join(" › ") : "폴더 미지정"}</td>
            <td className={`${cell} text-body`}>{document.owner_id}{department && <div className="mt-1 text-xs text-muted">{department.folder_id ? <Link href={`/documents?folder=${encodeURIComponent(department.folder_id)}`} className="hover:text-accent">{department.name}</Link> : department.name}</div>}</td>
            <td className={cell}><span title={document.follows_folder ? "폴더 범위 따름" : undefined} className={`inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${isPublic ? "bg-done-soft text-done" : "bg-sunken text-body"}`}>{isPublic ? "조직 공개" : "제한"}</span></td>
            <td className={`${cell} whitespace-nowrap text-body`}><time dateTime={document.updated_at}>{dateLabel(document.updated_at)}</time></td>
            <td className={cell}><DocumentStatusBadge document={document} variant="dot" /></td>
          </tr>;
        })}
      </tbody>
    </table>
  );
}
