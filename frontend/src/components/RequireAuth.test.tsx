import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { RequireAuth } from "./RequireAuth";

const replace = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => pathname,
}));

function authStatus(authenticated: boolean): Response {
  return new Response(
    JSON.stringify({ authenticated, username: authenticated ? "alice" : null, is_admin: false }),
    { headers: { "Content-Type": "application/json" } },
  );
}

describe("RequireAuth", () => {
  beforeEach(() => {
    replace.mockClear();
    pathname = "/";
    vi.unstubAllGlobals();
  });

  it("익명을 로그인 화면으로 보내고 문서 내용을 그리지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(authStatus(false)));

    render(<AuthProvider><RequireAuth><p>문서 목록</p></RequireAuth></AuthProvider>);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("문서 목록")).not.toBeInTheDocument();
  });

  it("로그인한 사용자에게는 화면을 그대로 보여준다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(authStatus(true)));

    render(<AuthProvider><RequireAuth><p>문서 목록</p></RequireAuth></AuthProvider>);

    expect(await screen.findByText("문서 목록")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("로그인 화면 자신은 익명에게도 그대로 열어 둔다", async () => {
    // 여기서 되돌려 보내면 로그인할 방법이 사라진다.
    pathname = "/login";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(authStatus(false)));

    render(<AuthProvider><RequireAuth><p>로그인 폼</p></RequireAuth></AuthProvider>);

    expect(await screen.findByText("로그인 폼")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("확인이 끝나기 전에는 로그인 화면으로 보내지 않는다", async () => {
    // 초기 상태는 익명이다. 확인 중에 보내면 새로고침마다 로그인으로 튄다.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    render(<AuthProvider><RequireAuth><p>문서 목록</p></RequireAuth></AuthProvider>);

    await waitFor(() => expect(screen.getByText("불러오는 중…")).toBeInTheDocument());
    expect(replace).not.toHaveBeenCalled();
  });
});
