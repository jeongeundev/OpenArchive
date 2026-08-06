import type { DocumentDetail } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

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
    <section className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-4xl font-semibold text-white">{document.title}</h1>
        <StatusBadge status={document.embedding_status} />
      </div>

      <dl className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-neutral-500">파일명</dt>
          <dd className="mt-1 text-neutral-300">{document.filename ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">유형</dt>
          <dd className="mt-1 uppercase text-neutral-300">{document.content_type}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">공개범위</dt>
          <dd className="mt-1 text-neutral-300">
            {document.visibility === "public" ? "공개" : "비공개"}
          </dd>
        </div>
        <div>
          <dt className="text-neutral-500">소유자</dt>
          <dd className="mt-1 text-neutral-300">{document.owner_id}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">생성 일시</dt>
          <dd className="mt-1 text-neutral-300">{formatDate(document.created_at)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">수정 일시</dt>
          <dd className="mt-1 text-neutral-300">{formatDate(document.updated_at)}</dd>
        </div>
      </dl>

      <p className="mt-6 border-t border-neutral-800 pt-4 text-sm text-neutral-500">
        {indexStatus(document)}
      </p>
    </section>
  );
}
