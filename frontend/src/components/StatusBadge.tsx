// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { DocumentSummary, EmbeddingStatus } from "@/lib/types";

type Tone = "wait" | "inflight" | "done" | "failed";

/** 연한 배경 알약 + 점 + 한글. 색만으로 상태를 말하지 않도록 대기는 빈 점, 처리 중은 깜빡이는 점이다. */
const TONE: Record<Tone, { badge: string; dot: string }> = {
  wait: { badge: "bg-wait-soft text-wait", dot: "border border-current" },
  inflight: { badge: "bg-accent-soft text-accent", dot: "bg-current motion-safe:animate-pulse" },
  done: { badge: "bg-done-soft text-done", dot: "bg-current" },
  failed: { badge: "bg-failed-soft text-failed", dot: "bg-current" },
};

const STATUS_DISPLAY: Record<EmbeddingStatus, { label: string; tone: Tone }> = {
  pending: { label: "대기 중", tone: "wait" },
  processing: { label: "처리 중…", tone: "inflight" },
  ready: { label: "완료", tone: "done" },
  error: { label: "실패", tone: "failed" },
};

function Badge({ label, tone }: { label: string; tone: Tone }): React.ReactElement {
  const style = TONE[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${style.badge}`}
    >
      <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${style.dot}`} />
      {label}
    </span>
  );
}

export function StatusBadge({ status }: { status: EmbeddingStatus }): React.ReactElement {
  return <Badge {...STATUS_DISPLAY[status]} />;
}

const EXTRACTION_DISPLAY = {
  pending: { label: "텍스트 인식 중", tone: "wait" },
  failed: { label: "텍스트 인식 실패", tone: "failed" },
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
  return <Badge {...EXTRACTION_DISPLAY[document.extraction_status]} />;
}
