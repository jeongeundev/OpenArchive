import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRelated } from "./useRelated";

const related = {
  items: [],
  identical: [],
  based_on_version: 1,
  reason: null,
};
const suggestions = { items: [], based_on_version: 1, reason: null };

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

describe("useRelated", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("관련 문서와 태그 추천을 병렬로 조회한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(related))
      .mockResolvedValueOnce(jsonResponse(suggestions));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useRelated("document-1", 1));
    await flushRequest();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledWith("/api/documents/document-1/related", expect.anything());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/documents/document-1/tag-suggestions",
      expect.anything(),
    );
    expect(result.current.related).toEqual(related);
    expect(result.current.suggestions).toEqual(suggestions);
  });

  it("chunkVersion이 바뀔 때만 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(jsonResponse(url.endsWith("/related") ? related : suggestions)),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = renderHook(
      ({ version }) => useRelated("document-1", version),
      { initialProps: { version: null as number | null } },
    );
    await flushRequest();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    rerender({ version: null });
    await flushRequest();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    rerender({ version: 1 });
    await flushRequest();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("API 실패를 오류 문자열로 제공하고 화면 데이터를 비운 상태로 유지한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(jsonResponse({ detail: "추천을 불러오지 못했습니다." }, 503)),
      ),
    );

    const { result } = renderHook(() => useRelated("document-1", 1));
    await flushRequest();

    expect(result.current.loading).toBe(false);
    expect(result.current.related).toBeNull();
    expect(result.current.suggestions).toBeNull();
    expect(result.current.error).toBe("추천을 불러오지 못했습니다.");
  });
});
