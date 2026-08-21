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
    expect(screen.getByRole("link", { name: "문서 진단" })).toHaveAttribute(
      "href",
      "/diagnostics",
    );
    expect(screen.queryByRole("link", { name: "사용자 관리" })).not.toBeInTheDocument();
  });

  it("운영 화면을 사용자 내비게이션에 노출하지 않는다", async () => {
    // `/admin/status`는 관측 채널이라 사용자 메뉴에 두지 않는다 (UI_GUIDE 디자인 원칙 3·4).
    // 문서 진단은 "내가 볼 수 있는 문서"의 상태라 사용자 화면이며 `/diagnostics`에 있다.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "root",
      is_admin: true,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    await screen.findByText("root");
    const menu = screen.getByRole("navigation", { name: "주요 메뉴" });
    const hrefs = Array.from(menu.querySelectorAll("a")).map((link) => link.getAttribute("href"));
    expect(hrefs).not.toContain("/admin/status");
    expect(hrefs).toContain("/diagnostics");
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

  it("사용자명이 계정 설정으로 가는 링크다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "alice" })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("익명에게는 계정 설정 진입점을 주지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><SiteHeader /></AuthProvider>);

    await screen.findByRole("link", { name: "로그인" });
    const hrefs = Array.from(document.querySelectorAll("a")).map((link) =>
      link.getAttribute("href"),
    );
    expect(hrefs).not.toContain("/settings");
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
