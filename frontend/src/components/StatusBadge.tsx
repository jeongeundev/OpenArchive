import type { DocumentSummary, EmbeddingStatus } from "@/lib/types";

const STATUS_DISPLAY: Record<EmbeddingStatus, { label: string; className: string }> = {
  pending: {
    label: "대기 중",
    className: "bg-[#a3a3a3]/10 text-[#a3a3a3]",
  },
  processing: {
    label: "처리 중…",
    className: "bg-[#a3a3a3]/10 text-[#a3a3a3]",
  },
  ready: {
    label: "완료",
    className: "bg-[#22c55e]/10 text-[#22c55e]",
  },
  error: {
    label: "실패",
    className: "bg-[#ef4444]/10 text-[#ef4444]",
  },
};

export function StatusBadge({ status }: { status: EmbeddingStatus }): React.ReactElement {
  const display = STATUS_DISPLAY[status];

  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${display.className}`}>
      {display.label}
    </span>
  );
}

const EXTRACTION_DISPLAY = {
  pending: { label: "텍스트 인식 중", className: "bg-[#a3a3a3]/10 text-[#a3a3a3]" },
  failed: { label: "텍스트 인식 실패", className: "bg-[#ef4444]/10 text-[#ef4444]" },
} as const;

/** 문서 한 건의 상태 배지. 텍스트 인식이 끝나지 않은 문서는 임베딩 상태가 의미 없으므로 인식 상태를 먼저 보인다 (ADR-052). */
export function DocumentStatusBadge({
  document,
}: {
  document: Pick<DocumentSummary, "embedding_status" | "extraction_status">;
}): React.ReactElement {
  if (document.extraction_status === "done") {
    return <StatusBadge status={document.embedding_status} />;
  }
  const display = EXTRACTION_DISPLAY[document.extraction_status];
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${display.className}`}>
      {display.label}
    </span>
  );
}
