import { renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";

import { useUnmountSignal } from "./useUnmountSignal";

describe("useUnmountSignal", () => {
  it("마운트된 동안에는 살아 있고, 언마운트하면 취소된다", () => {
    const { result, unmount } = renderHook(() => useUnmountSignal());
    const signal = result.current();

    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("StrictMode의 정리→재마운트 뒤에도 살아 있는 signal을 준다", () => {
    const { result } = renderHook(() => useUnmountSignal(), { wrapper: StrictMode });

    expect(result.current()?.aborted).toBe(false);
  });
});
