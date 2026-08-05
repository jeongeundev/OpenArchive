import type { EmbeddingStatus } from "@/lib/types";

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
