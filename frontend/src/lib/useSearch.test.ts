import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SearchResponse } from "./types";
import { useSearch } from "./useSearch";

const response: SearchResponse = { items: [], sql: "SELECT actual_sql" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useSearch", () => {
  beforeEach(() => window.localStorage.clear());

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("run을 호출할 때 입력값으로 검색 요청을 보낸다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSearch());

    await act(async () => {
      result.current.run({ query: "OpenSQL", tags: ["운영"], contentType: "md", folderId: null, k: 5 });
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/search",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          query: "OpenSQL",
          tags: ["운영"],
          content_type: "md",
          k: 5,
        }),
      }),
    );
    expect(result.current.response).toEqual(response);
  });

  it("실패하면 detail을 표시하고 이전 검색 응답을 유지한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(response))
      .mockResolvedValueOnce(jsonResponse({ detail: "검색할 수 없습니다." }, 500));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSearch());

    await act(async () => {
      result.current.run({ query: "첫 검색", tags: [], contentType: null, folderId: null, k: 10 });
    });
    await act(async () => {
      result.current.run({ query: "두 번째 검색", tags: [], contentType: null, folderId: null, k: 10 });
    });

    expect(result.current.response).toEqual(response);
    expect(result.current.error).toBe("검색할 수 없습니다.");
  });

  it("빈 질의는 요청하지 않는다", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSearch());

    act(() => {
      result.current.run({ query: "   ", tags: [], contentType: null, folderId: null, k: 10 });
    });

    expect(fetchMock).not.toHaveBeenCalled();
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

describe("useSearch 취소", () => {
  const input = { query: "정합성", tags: [], contentType: null, folderId: null, k: 10 };

  it("새 검색이 이전 검색을 취소하고, 언마운트하면 마지막 검색도 취소한다", () => {
    const fetchMock = pendingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const { result, unmount } = renderHook(() => useSearch());
    act(() => result.current.run(input));
    act(() => result.current.run({ ...input, query: "버전" }));

    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(false);
    unmount();
    expectAllAborted(fetchMock);
    vi.unstubAllGlobals();
  });

  it("취소된 이전 검색은 오류도 로딩 해제도 남기지 않는다", async () => {
    let first: (value: Response) => void = () => {};
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            first = resolve;
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      )
      .mockImplementationOnce(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useSearch());
    act(() => result.current.run(input));
    act(() => result.current.run({ ...input, query: "버전" }));
    await act(async () => {
      first(new Response("{}"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(true);
    vi.unstubAllGlobals();
  });
});
