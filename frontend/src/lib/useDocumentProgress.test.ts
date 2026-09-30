import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentProgress } from "./types";
import { useDocumentProgress } from "./useDocumentProgress";

const progress: DocumentProgress = {
  extracting: 1,
  extraction_failed: 0,
  pending: 2,
  processing: 1,
  ready: 60,
  error: 0,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function flushRequest(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useDocumentProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("단계별 집계를 조회하고 2초마다 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(progress));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocumentProgress());
    await flushRequest();

    expect(result.current.progress).toEqual(progress);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/progress");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("조회에 실패하면 마지막 집계를 유지한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(progress))
      .mockResolvedValueOnce(jsonResponse({ detail: "실패" }, 500));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocumentProgress());
    await flushRequest();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(result.current.progress).toEqual(progress);
  });

  it("언마운트하면 진행 중인 조회를 취소한다", () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useDocumentProgress());
    unmount();

    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});
