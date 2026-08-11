import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SystemStatus } from "./types";
import { useSystemStatus } from "./useSystemStatus";

const status: SystemStatus = {
  node_address: "192.168.64.4",
  node_port: 6432,
  jobs: { pending: 1, processing: 0, recovery_pending: 0, error: 0 },
  zombie_timeout_minutes: 5,
  last_job_finished_at: null,
  inconsistent_documents: 0,
  embedding_provider: "fake",
};

function response(body: unknown, code = 200): Response {
  return new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
}

describe("useSystemStatus", () => {
  beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("마운트 직후 조회하고 2초 뒤 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(status));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useSystemStatus());
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("언마운트하면 더 조회하지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(status));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = renderHook(() => useSystemStatus());
    await flush();
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("후속 조회가 실패해도 마지막 성공 값과 오류를 함께 제공한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(status))
      .mockResolvedValueOnce(response({ detail: "DB 연결이 끊겼습니다." }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSystemStatus());
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.status).toEqual(status);
    expect(result.current.error).toBe("DB 연결이 끊겼습니다.");
  });
});
