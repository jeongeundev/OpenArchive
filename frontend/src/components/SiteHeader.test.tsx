import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { SiteHeader } from "./SiteHeader";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
}

describe("SiteHeader", () => {
  it("익명에게 로그인 링크만 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "로그인" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.queryByRole("link", { name: "사용자 관리" })).not.toBeInTheDocument();
  });

  it("일반 사용자에게 로그아웃을 제공하되 사용자 관리는 숨긴다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "사용자 관리" })).not.toBeInTheDocument();
  });

  it("관리자에게만 사용자 관리 진입점을 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "admin",
      is_admin: true,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "사용자 관리" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
  });

  it("로그아웃하면 즉시 익명 내비게이션으로 돌아간다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ authenticated: true, username: "alice", is_admin: false }))
      .mockResolvedValueOnce(response({ authenticated: false, username: null, is_admin: false }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><SiteHeader /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "로그아웃" }));

    await waitFor(() => expect(screen.getByRole("link", { name: "로그인" })).toBeInTheDocument());
  });
});
