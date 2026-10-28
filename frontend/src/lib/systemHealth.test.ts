// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import { outageSeconds, outageNodes, outageAuditUrl, attentionCount, attentionTarget, bannerKind, failureTotal, stageBadge } from "./systemHealth";
import type { SystemStatus } from "./types";

const healthy: SystemStatus = {
  node_address: "192.168.64.4", node_port: 6432,
  jobs: { pending: 3, processing: 1, recovery_pending: 1, error: 0 },
  job_lease_seconds: 60, last_job_finished_at: null,
  inconsistent_documents: 0, stale_edge_documents: 2, extraction_pending: 1, extraction_failed: 0,
  preview_pending: 1, preview_failed: 0, preview_unavailable: 0, embedding_provider: "BAAI/bge-m3",
};

describe("systemHealth", () => {
  it("처리 실패는 인식 실패·임베딩 오류·미리보기 실패의 합이다", () => {
    expect(failureTotal(healthy)).toBe(0);
    expect(failureTotal({ ...healthy, extraction_failed: 1, jobs: { ...healthy.jobs, error: 2 }, preview_failed: 4 })).toBe(7);
  });

  it("확인이 필요한 항목은 변환기 없음 판 수다", () => {
    expect(attentionCount(healthy)).toBe(0);
    expect(attentionCount({ ...healthy, preview_unavailable: 3 })).toBe(3);
  });

  it("실패와 정합성 불일치가 모두 0이면 정상 배너, 하나라도 있으면 실패 배너다", () => {
    expect(bannerKind(healthy)).toBe("ok");
    expect(bannerKind({ ...healthy, preview_unavailable: 1 })).toBe("ok");
    expect(bannerKind({ ...healthy, jobs: { ...healthy.jobs, error: 1 } })).toBe("failed");
    expect(bannerKind({ ...healthy, inconsistent_documents: 1 })).toBe("failed");
  });

  it("단계별 배지는 실패가 확인 필요보다 앞선다", () => {
    expect(stageBadge(healthy, "extraction")).toBe("ok");
    expect(stageBadge({ ...healthy, extraction_failed: 1 }, "extraction")).toBe("failed");
    expect(stageBadge(healthy, "embedding")).toBe("ok");
    expect(stageBadge({ ...healthy, jobs: { ...healthy.jobs, error: 1 } }, "embedding")).toBe("failed");
    expect(stageBadge(healthy, "relations")).toBe("ok");
    expect(stageBadge(healthy, "preview")).toBe("ok");
    expect(stageBadge({ ...healthy, preview_unavailable: 1 }, "preview")).toBe("attention");
    expect(stageBadge({ ...healthy, preview_unavailable: 1, preview_failed: 1 }, "preview")).toBe("failed");
  });

  it("확인할 항목 링크는 화면 순서상 첫 확인 칸을 가리키고, 없으면 null이다", () => {
    expect(attentionTarget(healthy)).toBeNull();
    expect(attentionTarget({ ...healthy, preview_unavailable: 1 })).toBe("preview-unavailable-notice");
    expect(attentionTarget({ ...healthy, preview_unavailable: 1, jobs: { ...healthy.jobs, error: 1 } })).toBe("stage-embedding");
    expect(attentionTarget({ ...healthy, inconsistent_documents: 1, extraction_failed: 1 })).toBe("consistency-card");
    expect(attentionTarget({ ...healthy, preview_failed: 1 })).toBe("stage-preview");
  });
});

describe("장애 기록 표시 계산", () => {
  it("중단 초를 반올림하고 같은 노드는 한 번 표시하며 감사 시각을 1분 앞당긴다", () => {
    expect(outageSeconds({ first_failure_at: "2026-10-28T06:10:02Z", recovered_at: "2026-10-28T06:10:35.4Z" })).toBe(33);
    expect(outageNodes({ node_before: "a:5432", node_after: "a:5432" })).toBe("a:5432");
    expect(outageNodes({ node_before: "a", node_after: "b" })).toBe("a → b");
    expect(outageAuditUrl("2026-10-28T06:10:02Z")).toBe("/admin/audit?since=2026-10-28T06%3A09%3A02.000Z");
  });
});
