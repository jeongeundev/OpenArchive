// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SystemStatus } from "@/lib/types";
import AdminStatusPage from "./page";

const refresh = vi.fn();
const status: SystemStatus = {
  node_address: "192.168.64.4", node_port: 6432,
  jobs: { pending: 0, processing: 0, recovery_pending: 0, error: 0 },
  job_lease_seconds: 60, last_job_finished_at: null,
  inconsistent_documents: 0, stale_edge_documents: 0, extraction_pending: 0, extraction_failed: 0,
  preview_pending: 0, preview_failed: 0, preview_unavailable: 0, embedding_provider: "BAAI/bge-m3",
};

vi.mock("@/lib/useSystemStatus", () => ({
  useSystemStatus: () => ({ status, loading: false, error: null, checkedAt: new Date(2026, 9, 28, 14, 31, 5), refresh }),
}));
vi.mock("@/components/ErrorDocuments", () => ({ ErrorDocuments: () => null }));

describe("AdminStatusPage", () => {
  it("머리에 관리 표시·제목·마지막 확인 시각을 보이고 새로고침이 refresh를 부른다", () => {
    render(<AdminStatusPage />);
    expect(screen.getByText("관리 · 운영·데모 전용")).toHaveClass("text-accent");
    expect(screen.getByRole("heading", { level: 1, name: "시스템 상태" })).toBeInTheDocument();
    expect(screen.getByText("연결이 잠시 끊겨도 마지막 관측값을 유지하며, 짧은 중단 뒤 자동으로 복구됩니다.")).toBeInTheDocument();
    expect(screen.getByText("마지막 확인 오후 2:31:05")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    expect(refresh).toHaveBeenCalledOnce();
  });
});
