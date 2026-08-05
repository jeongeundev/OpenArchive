import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "./types";
import { useDocuments } from "./useDocuments";

const document: DocumentSummary = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  version: 1,
  owner_id: "alice",
  visibility: "public",
  tags: ["OpenSQL"],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
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

describe("useDocuments", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("마운트 직후 조회하고 기본 2초마다 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([document]));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocuments());

    await flushRequest();
    expect(result.current.loading).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("언마운트하면 폴링 타이머를 정리한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([document]));
    vi.stubGlobal("fetch", fetchMock);

    const { result, unmount } = renderHook(() => useDocuments());
    await flushRequest();
    expect(result.current.loading).toBe(false);
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("이전 조회가 끝나지 않았으면 다음 폴링을 건너뛴다", async () => {
    let resolveRequest: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocuments());
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveRequest?.(jsonResponse([document]));
    await flushRequest();
  });

  it("폴링 실패 시 마지막 성공 목록을 유지하고 오류를 표시한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([document]))
      .mockResolvedValueOnce(jsonResponse({ detail: "잠시 연결할 수 없습니다." }, 503));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocuments());
    await flushRequest();
    expect(result.current.documents).toEqual([document]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(result.current.documents).toEqual([document]);
    expect(result.current.error).toBe("잠시 연결할 수 없습니다.");
  });
});
