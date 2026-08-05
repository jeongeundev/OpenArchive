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
      result.current.run({ query: "OpenSQL", tags: ["운영"], contentType: "md", k: 5 });
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
      .mockResolvedValueOnce(jsonResponse({ detail: "검색할 수 없습니다." }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSearch());

    await act(async () => {
      result.current.run({ query: "첫 검색", tags: [], contentType: null, k: 10 });
    });
    await act(async () => {
      result.current.run({ query: "두 번째 검색", tags: [], contentType: null, k: 10 });
    });

    expect(result.current.response).toEqual(response);
    expect(result.current.error).toBe("검색할 수 없습니다.");
  });

  it("빈 질의는 요청하지 않는다", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSearch());

    act(() => {
      result.current.run({ query: "   ", tags: [], contentType: null, k: 10 });
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
