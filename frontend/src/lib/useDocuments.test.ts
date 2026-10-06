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
  extraction_status: "done",
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
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.includes("/count") ? { total: 1 } : [document])));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocuments());

    await flushRequest();
    expect(result.current.loading).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("목록과 건수에 같은 필터를 보내고 변경된 조건을 조회한다", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.includes("/count") ? { total: 7 } : [document])));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ q }) => useDocuments({ q, contentType: "hwp", tag: "보안", status: "ready", sort: "title", limit: 50, offset: 0 }), { initialProps: { q: "출장" } });
    await flushRequest();
    expect(result.current.documents).toEqual([document]);
    expect(result.current.total).toBe(7);
    rerender({ q: "휴가" });
    await flushRequest();
    for (const [index, q] of [[0, "출장"], [2, "휴가"]] as const) {
      const list = new URL(fetchMock.mock.calls[index][0], "http://localhost");
      const count = new URL(fetchMock.mock.calls[index + 1][0], "http://localhost");
      expect(Object.fromEntries(count.searchParams)).toEqual({ q, content_type: "hwp", tag: "보안", status: "ready" });
      for (const [key, value] of count.searchParams) expect(list.searchParams.get(key)).toBe(value);
      expect(fetchMock.mock.calls[index][1].signal).toBe(fetchMock.mock.calls[index + 1][1].signal);
    }
  });

  it("건수 실패 시 목록은 표시하고 건수는 null과 오류를 반환한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve(url.includes("/count") ? jsonResponse({ detail: "건수 실패" }, 500) : jsonResponse([document]))));
    const { result } = renderHook(() => useDocuments());
    await flushRequest();
    expect(result.current.documents).toEqual([document]);
    expect(result.current.total).toBeNull();
    expect(result.current.error).toBe("건수 실패");
  });

  it("페이지 창을 주면 그 구간만 조회한다", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.includes("/count") ? { total: 1 } : [document])));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocuments({ limit: 50, offset: 50 }));
    await flushRequest();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents?limit=50&offset=50");
  });

  it("페이지를 바꾸면 새 구간을 바로 조회한다", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.includes("/count") ? { total: 1 } : [document])));
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = renderHook(({ offset }) => useDocuments({ limit: 50, offset }), {
      initialProps: { offset: 0 },
    });
    await flushRequest();
    rerender({ offset: 50 });
    await flushRequest();

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/documents?limit=50&offset=0",
      "/api/documents/count",
      "/api/documents?limit=50&offset=50",
      "/api/documents/count",
    ]);
  });

  it("언마운트하면 폴링 타이머를 정리한다", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.includes("/count") ? { total: 1 } : [document])));
    vi.stubGlobal("fetch", fetchMock);

    const { result, unmount } = renderHook(() => useDocuments());
    await flushRequest();
    expect(result.current.loading).toBe(false);
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("이전 조회가 끝나지 않았으면 다음 폴링을 건너뛴다", async () => {
    const resolveRequests: ((response: Response) => void)[] = [];
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveRequests.push(resolve);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocuments());
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    resolveRequests[0](jsonResponse([document]));
    resolveRequests[1](jsonResponse({ total: 1 }));
    await flushRequest();
  });

  it("폴링 실패 시 마지막 성공 목록을 유지하고 오류를 표시한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([document]))
      .mockResolvedValueOnce(jsonResponse({ total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ detail: "잠시 연결할 수 없습니다." }, 500))
      .mockResolvedValueOnce(jsonResponse({ total: 1 }));
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

// 응답하지 않는 서버 — 화면을 떠날 때 요청이 취소되는지만 본다.
function pendingFetch() {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}));
}

function expectAllAborted(fetchMock: ReturnType<typeof pendingFetch>): void {
  expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
  for (const [, init] of fetchMock.mock.calls) expect(init?.signal?.aborted).toBe(true);
}

describe("useDocuments 취소", () => {
  it("언마운트하면 진행 중인 조회를 취소한다", () => {
    const fetchMock = pendingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useDocuments());
    unmount();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(fetchMock.mock.calls[1][1]?.signal);
    expectAllAborted(fetchMock);
    vi.unstubAllGlobals();
  });
});
