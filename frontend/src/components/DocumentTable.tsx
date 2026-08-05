import Link from "next/link";

import type { DocumentSummary } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function DocumentTable({
  documents,
}: {
  documents: DocumentSummary[];
}): React.ReactElement {
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
      <table className="w-full text-left text-sm text-neutral-300">
        <thead className="border-b border-neutral-800 text-xs text-neutral-400">
          <tr>
            <th className="px-4 py-3 font-medium" scope="col">제목</th>
            <th className="px-4 py-3 font-medium" scope="col">유형</th>
            <th className="px-4 py-3 font-medium" scope="col">태그</th>
            <th className="px-4 py-3 font-medium" scope="col">공개범위</th>
            <th className="px-4 py-3 font-medium" scope="col">상태</th>
            <th className="px-4 py-3 font-medium" scope="col">수정일</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-800">
          {documents.length === 0 ? (
            <tr>
              <td className="px-4 py-8 text-neutral-500" colSpan={6}>
                아직 문서가 없습니다.
              </td>
            </tr>
          ) : (
            documents.map((document) => (
              <tr key={document.id}>
                <td className="px-4 py-3 font-medium">
                  <Link
                    className="text-white hover:text-[#0ea5e9]"
                    href={`/documents/${document.id}`}
                  >
                    {document.title}
                  </Link>
                </td>
                <td className="px-4 py-3 uppercase text-neutral-400">
                  {document.content_type}
                </td>
                <td className="px-4 py-3 text-neutral-400">
                  {document.tags.length > 0 ? document.tags.join(", ") : "—"}
                </td>
                <td className="px-4 py-3">
                  {document.visibility === "public" ? "공개" : "비공개"}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={document.embedding_status} />
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-neutral-400">
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
