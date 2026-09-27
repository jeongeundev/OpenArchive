import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listDocuments } from "@/lib/api";
import { RetryNotice } from "./RetryNotice";

describe("RetryNotice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.999999);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("재시도하는 동안만 중립 안내를 보이고 인프라는 드러내지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("{}", { status: 503 }))
        .mockResolvedValueOnce(new Response("[]")),
    );
    render(<RetryNotice />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    const result = listDocuments();
    await act(() => vi.advanceTimersByTimeAsync(0));

    // UI_GUIDE 원칙 3 — 노드·장애·DB 같은 말을 쓰지 않는다.
    expect(screen.getByRole("status")).toHaveTextContent(
      "연결이 원활하지 않아 다시 시도하는 중입니다.",
    );

    await act(() => vi.advanceTimersByTimeAsync(1000));
    await result;

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
