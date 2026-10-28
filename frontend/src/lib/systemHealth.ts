// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { DbOutage, SystemStatus } from "./types";

export type HealthKind = "ok" | "attention" | "failed";
export type Stage = "extraction" | "embedding" | "relations" | "preview";

export function failureTotal(status: SystemStatus): number {
  return status.extraction_failed + status.jobs.error + status.preview_failed;
}

export function attentionCount(status: SystemStatus): number {
  return status.preview_unavailable;
}

export function bannerKind(status: SystemStatus): "ok" | "failed" {
  return failureTotal(status) === 0 && status.inconsistent_documents === 0 ? "ok" : "failed";
}

export function stageBadge(status: SystemStatus, stage: Stage): HealthKind {
  switch (stage) {
    case "extraction": return status.extraction_failed > 0 ? "failed" : "ok";
    case "embedding": return status.jobs.error > 0 ? "failed" : "ok";
    // 미반영은 관계 잡이 처리 중이라는 뜻이라 실패로 단정하지 않는다.
    case "relations": return "ok";
    case "preview": return status.preview_failed > 0 ? "failed" : status.preview_unavailable > 0 ? "attention" : "ok";
  }
}

// 「확인할 항목 보기」가 가리킬 칸의 id — 화면 위에서부터 첫 칸.
export function attentionTarget(status: SystemStatus): string | null {
  if (status.inconsistent_documents > 0) return "consistency-card";
  if (status.extraction_failed > 0) return "stage-extraction";
  if (status.jobs.error > 0) return "stage-embedding";
  if (status.preview_failed > 0) return "stage-preview";
  if (status.preview_unavailable > 0) return "preview-unavailable-notice";
  return null;
}


export const STATUS_UNRESPONSIVE_MS = 5_000;

export function outageSeconds(outage: Pick<DbOutage, "first_failure_at" | "recovered_at">): number {
  return Math.round((Date.parse(outage.recovered_at) - Date.parse(outage.first_failure_at)) / 1000);
}

export function outageNodes(outage: Pick<DbOutage, "node_before" | "node_after">): string {
  const before = outage.node_before ?? "기록 없음";
  const after = outage.node_after ?? "기록 없음";
  return before === after ? before : `${before} → ${after}`;
}

export function outageAuditUrl(firstFailureAt: string): string {
  const since = new Date(Date.parse(firstFailureAt) - 60_000).toISOString();
  return `/admin/audit?since=${encodeURIComponent(since)}`;
}
