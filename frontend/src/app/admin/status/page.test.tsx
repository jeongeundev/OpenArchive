// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SystemStatus } from "@/lib/types";
import { OUTAGES_REFRESH_MS } from "@/lib/systemHealth";
import AdminStatusPage from "./page";

const refresh = vi.fn();
const clockState = vi.hoisted(() => ({ lastSuccessAt: null as Date | null }));
vi.mock("@/lib/api", () => ({ listOutages: vi.fn().mockResolvedValue([]) }));
import { listOutages } from "@/lib/api";
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); clockState.lastSuccessAt = null; });
const status: SystemStatus = {
  node_address: "192.168.64.4", node_port: 6432,
  jobs: { pending: 0, processing: 0, recovery_pending: 0, error: 0 },
  job_lease_seconds: 60, last_job_finished_at: null,
  inconsistent_documents: 0, stale_edge_documents: 0, extraction_pending: 0, extraction_failed: 0,
  preview_pending: 0, preview_failed: 0, preview_unavailable: 0, embedding_provider: "BAAI/bge-m3",
};

vi.mock("@/lib/useSystemStatus", () => ({
  useSystemStatus: () => ({ status, loading: false, error: null, checkedAt: new Date(2026, 9, 28, 14, 31, 5), lastSuccessAt: clockState.lastSuccessAt, refresh }),
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


it("응답이 매달려도 5초 뒤 배너를 바꾸고 복구 순간 기록을 다시 읽는다", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-28T06:10:02Z"));
  clockState.lastSuccessAt = new Date();
  const { rerender } = render(<AdminStatusPage />);
  await act(async () => { await Promise.resolve(); });
  expect(listOutages).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
  expect(screen.getByTestId("health-banner")).toHaveTextContent("DB 응답 없음 — 6초째");
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByTestId("health-banner")).toHaveTextContent("7초째");
  const beforeRecovery = vi.mocked(listOutages).mock.calls.length;
  clockState.lastSuccessAt = new Date();
  rerender(<AdminStatusPage />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByTestId("health-banner")).toHaveTextContent("모든 처리가 정상입니다");
  // 복구 순간 한 번 — 주기 조회와 별개로 바로 읽는다
  expect(listOutages).toHaveBeenCalledTimes(beforeRecovery + 1);
  // 짧은 간격의 정상 성공은 복구로 보지 않는다
  clockState.lastSuccessAt = new Date(Date.now() + 2000);
  rerender(<AdminStatusPage />);
  expect(listOutages).toHaveBeenCalledTimes(beforeRecovery + 1);
});

it("감시 루프가 복구 뒤 늦게 쓴 기록도 주기 조회로 나타난다", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-28T06:10:35Z"));
  clockState.lastSuccessAt = new Date();
  const outage = {
    id: 1, kind: "promotion" as const, last_ok_at: "2026-10-28T06:10:00Z", first_failure_at: "2026-10-28T06:10:02Z",
    recovered_at: "2026-10-28T06:10:35Z", node_before: "192.168.64.201:5432", node_after: "192.168.64.202:5432",
    timeline_before: 85, timeline_after: 86, failed_checks: 8, requests_retried: 3, requests_unavailable: 0, server_errors: 0,
  };
  vi.mocked(listOutages).mockResolvedValueOnce([]).mockResolvedValue([outage]);
  render(<AdminStatusPage />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByText("타임라인 85 → 86")).not.toBeInTheDocument();
  await act(async () => { clockState.lastSuccessAt = new Date(); await vi.advanceTimersByTimeAsync(OUTAGES_REFRESH_MS); });
  expect(listOutages).toHaveBeenCalledTimes(2);
  expect(screen.getByText("타임라인 85 → 86")).toBeInTheDocument();
});

it("새로고침은 장애 기록도 다시 읽는다", async () => {
  render(<AdminStatusPage />);
  await act(async () => { await Promise.resolve(); });
  const before = vi.mocked(listOutages).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
  await act(async () => { await Promise.resolve(); });
  expect(listOutages).toHaveBeenCalledTimes(before + 1);
});
