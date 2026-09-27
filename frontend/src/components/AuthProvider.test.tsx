import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthProvider";

describe("AuthProvider", () => {
  it("언마운트하면 진행 중인 로그인 상태 조회를 취소한다", () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><p>화면</p></AuthProvider>);
    unmount();

    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});
