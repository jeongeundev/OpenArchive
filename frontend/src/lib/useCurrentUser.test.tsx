import { renderHook } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { useCurrentUser } from "./useCurrentUser";
import { setCurrentUser } from "./user";

function Probe(): React.ReactElement {
  return <span>{useCurrentUser() ?? "익명"}</span>;
}

describe("useCurrentUser", () => {
  beforeEach(() => localStorage.clear());

  it("저장된 데모 사용자를 첫 렌더에서 반환한다", () => {
    setCurrentUser("alice");

    const { result } = renderHook(() => useCurrentUser());

    expect(result.current).toBe("alice");
  });

  it("저장된 사용자가 없으면 null을 반환한다", () => {
    const { result } = renderHook(() => useCurrentUser());

    expect(result.current).toBeNull();
  });

  it("서버 렌더에서는 저장된 사용자가 있어도 익명으로 그린다", () => {
    // 프리렌더에는 localStorage가 없다. 서버 스냅샷을 익명으로 고정해야 첫 클라이언트
    // 렌더가 프리렌더 HTML과 같아지고, 하이드레이션이 깨지지 않는다.
    setCurrentUser("alice");

    expect(renderToString(<Probe />)).toContain("익명");
  });
});
