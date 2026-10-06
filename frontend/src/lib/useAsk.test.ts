import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAsk } from "./useAsk";

const input = { query: "장애 복구", tags: [], contentType: null, folderId: null, k: 5 };

describe("useAsk", () => {
  afterEach(() => vi.unstubAllGlobals());

  // 생성은 수십 초 걸린다. 그 사이 새로 검색하면 이전 검색어의 답이 늦게 와도 보이면 안 된다.
  it("reset 뒤에 도착한 이전 답은 버리고 요청도 취소한다", async () => {
    let resolve: (response: Response) => void = () => {};
    const fetchMock = vi.fn(
      () => new Promise<Response>((done) => {
        resolve = done;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAsk());

    act(() => result.current.run(input));
    expect(result.current.loading).toBe(true);
    act(() => result.current.reset());
    await act(async () => {
      resolve(new Response(JSON.stringify({ status: "answered", answer: "늦은 답", detail: null, sources: [], items: [] })));
    });

    expect(result.current.response).toBeNull();
    expect(result.current.loading).toBe(false);
    expect((fetchMock.mock.calls[0] as unknown[] as [string, RequestInit])[1].signal?.aborted).toBe(true);
  });
});
