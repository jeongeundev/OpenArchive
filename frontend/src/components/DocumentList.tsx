// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";

import { scopeSummary } from "@/lib/scope";
import type { DocumentSummary } from "@/lib/types";
import { OwnerLabel } from "./OwnerLabel";
import { DocumentStatusBadge } from "./StatusBadge";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * 문서 목록 — 행마다 두 줄(형식 · 제목 · 상태 / 위치 · 소유자 · 열람 범위 · 수정일 · 태그).
 * 제목은 상세로 가고(TC 245), 행의 나머지 영역과 「정보」 버튼은 오른쪽 정보 창을 연다.
 * 위치는 볼 수 있는 폴더 경로만 온다 — 없으면 자리째 빼 숨은 폴더의 흔적을 남기지 않는다.
 */
export function DocumentList({
  documents,
  emptyMessage = "아직 문서가 없습니다.",
  selectedId,
  onSelect,
}: {
  documents: DocumentSummary[];
  emptyMessage?: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
}): React.ReactElement {
  if (documents.length === 0) {
    return (
      <div className="rounded-lg border border-line bg-card p-6">
        <p className="text-sm text-muted">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-card">
      {documents.map((document) => {
        const selected = document.id === selectedId;
        const location = document.folder_path?.length ? document.folder_path.join(" › ") : null;
        const meta: { key: string; text: React.ReactNode; strong?: boolean; wrap?: boolean }[] = [
          ...(location !== null ? [{ key: "location", text: location, strong: true }] : []),
          { key: "owner", text: <OwnerLabel owner={document.owner_id} department={document.owner_department} /> },
          { key: "scope", text: scopeSummary(document) },
          { key: "updated", text: DATE_FORMATTER.format(new Date(document.updated_at)) },
          ...(document.tags.length > 0 ? [{ key: "tags", text: document.tags.join(", "), wrap: true }] : []),
        ];
        return (
          <li
            key={document.id}
            aria-current={selected ? "true" : undefined}
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("a, button")) return;
              onSelect(document.id);
            }}
            className={`cursor-pointer border-l-2 px-4 py-3 ${
              selected ? "border-l-accent bg-accent-soft" : "border-l-transparent hover:bg-sunken"
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 shrink-0 whitespace-nowrap rounded border border-line px-1.5 py-0.5 text-xs font-medium uppercase text-muted">
                {document.content_type}
              </span>
              <Link
                href={`/documents/${document.id}`}
                className="min-w-0 flex-1 break-words font-medium text-strong hover:text-accent"
              >
                {document.title}
              </Link>
              <div className="flex shrink-0 items-center gap-2">
                <DocumentStatusBadge document={document} />
                <button
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onSelect(document.id)}
                  className="rounded-lg border border-line px-2 py-0.5 text-xs text-body hover:bg-sunken"
                >
                  정보
                </button>
              </div>
            </div>
            <p className="mt-1 flex flex-wrap gap-x-1.5 pl-1 text-xs text-muted">
              {meta.map((item, index) => (
                <span key={item.key} className="flex gap-x-1.5">
                  {index > 0 ? <span aria-hidden="true">·</span> : null}
                  <span
                    data-testid="document-meta-item"
                    className={`${item.wrap ? "" : "whitespace-nowrap"} ${item.strong ? "font-medium text-body" : ""}`.trim()}
                  >
                    {item.text}
                  </span>
                </span>
              ))}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
