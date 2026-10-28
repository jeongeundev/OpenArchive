// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { AppShell } from "./AppShell";
import { getHealth } from "@/lib/api";
import { Sidebar } from "./Sidebar";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname, useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getHealth: vi.fn().mockResolvedValue(undefined),
}));

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
}

describe("Sidebar", () => {
  it("문서 상세에서는 문서만 현재 메뉴이며 홈은 아니다", () => {
    pathname = "/documents/doc-1";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "문서" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "홈" })).not.toHaveAttribute("aria-current");
    for (const [name, href] of [["홈", "/"], ["문서", "/documents"], ["AI 검색", "/search"], ["부서", "/departments"]]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    }
    pathname = "/";
  });

  it("health 성공과 실패를 서버 상태로 보인다", async () => {
    const { unmount } = render(<Sidebar />);
    expect(await screen.findByText("서버 연결됨")).toBeInTheDocument();
    expect(screen.getByText(window.location.host)).toBeInTheDocument();
    unmount();
    vi.mocked(getHealth).mockRejectedValueOnce(new Error("offline"));
    render(<Sidebar />);
    expect(await screen.findByText("서버 응답 없음")).toBeInTheDocument();
  });

  it("로그인 경로에는 사이드바 없이 본문과 푸터가 있다", () => {
    pathname = "/login";
    render(<AppShell>로그인 폼</AppShell>);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.getByText("로그인 폼")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    pathname = "/";
  });

  it("익명에게 로그인 링크만 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "로그인" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.getByRole("link", { name: "문서 진단" })).toHaveAttribute(
      "href",
      "/diagnostics",
    );
    expect(screen.queryByRole("link", { name: "외부 공유" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "사용자 관리" })).not.toBeInTheDocument();
    // 정리할 문서는 「내 문서」 목록이라 익명에게는 없다.
    expect(screen.queryByRole("link", { name: "정리할 문서" })).not.toBeInTheDocument();
  });

  it("로그인한 사용자에게 정리할 문서 화면을 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "정리할 문서" })).toHaveAttribute("href", "/organize");
  });

  it("로그인 사용자에 부서가 있으면 `이름과 둘째 줄 부서`로 보이고 부서는 부서 폴더 링크다 (D16)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "김민준",
      is_admin: false,
      department: { name: "IT전략실", folder_id: "f1" },
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "김민준" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "IT전략실" })).toHaveAttribute("href", "/documents?folder=f1");
    expect(screen.getByTestId("account-label")).toHaveTextContent(/^김민준IT전략실$/);
  });

  it("부서 폴더를 볼 수 없으면 부서 이름만 링크 없이, 부서가 없으면 이름만 보인다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "김민준",
      is_admin: false,
      department: { name: "IT전략실", folder_id: null },
    })));

    const { unmount } = render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByTestId("account-label")).toHaveTextContent(/^김민준IT전략실$/);
    expect(screen.queryByRole("link", { name: "IT전략실" })).not.toBeInTheDocument();
    unmount();

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "김민준",
      is_admin: false,
      department: null,
    })));
    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByTestId("account-label")).toHaveTextContent(/^김민준$/);
  });

  it("관리자 메뉴에 시스템 상태를 제공한다", async () => {
    // 문서 진단은 "내가 볼 수 있는 문서"의 상태라 사용자 화면이며 `/diagnostics`에 있다.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "root",
      is_admin: true,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    await screen.findByText("root");
    const menu = screen.getByRole("navigation", { name: "주요 메뉴" });
    const hrefs = Array.from(menu.querySelectorAll("a")).map((link) => link.getAttribute("href"));
    expect(screen.getByRole("link", { name: "시스템 상태" })).toHaveAttribute("href", "/admin/status");
    expect(hrefs).toContain("/diagnostics");
  });

  it("일반 사용자에게 로그아웃을 제공하되 사용자 관리는 숨긴다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "외부 공유" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "사용자 관리" })).not.toBeInTheDocument();
    expect(screen.queryByText("관리자 전용")).not.toBeInTheDocument();
    expect(screen.queryByText("관리", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "그룹 관리" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "감사 로그" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "시스템 상태" })).not.toBeInTheDocument();
  });

  it("관리자에게만 사용자 관리 진입점을 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "admin",
      is_admin: true,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "사용자 관리" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
    expect(screen.getByRole("link", { name: "외부 공유" })).toHaveAttribute("href", "/admin/shares");
    expect(screen.getByRole("link", { name: "그룹 관리" })).toHaveAttribute("href", "/admin/groups");
    expect(screen.getByRole("link", { name: "감사 로그" })).toHaveAttribute("href", "/admin/audit");
    expect(screen.getByText("관리자 전용")).toHaveClass("rounded-full", "bg-accent-soft", "text-accent");
    const paths = ["사용자 관리", "그룹 관리", "외부 공유", "감사 로그", "시스템 상태"].map((name) => {
      const link = screen.getByRole("link", { name });
      const svg = link.querySelector("svg");
      expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
      expect(svg).toHaveAttribute("stroke-width", "1.5");
      const path = svg?.querySelector("path")?.getAttribute("d");
      expect(path).toBeTruthy();
      expect(path).not.toBe("M4 4h16v16H4Z");
      return path;
    });
    expect(new Set(paths).size).toBe(5);
  });

  it("사용자명이 계정 설정으로 가는 링크다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><Sidebar /></AuthProvider>);

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

    render(<AuthProvider><Sidebar /></AuthProvider>);

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

    render(<AuthProvider><Sidebar /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "로그아웃" }));

    await waitFor(() => expect(screen.getByRole("link", { name: "로그인" })).toBeInTheDocument());
  });

  it.each([
    [{ authenticated: false, username: null, is_admin: false }, "로그인"],
    [{ authenticated: true, username: "alice", is_admin: false }, "로그아웃"],
  ])("로그인 여부와 무관하게 테마 버튼을 둔다 (%#)", async (me, anchor) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(me)));

    render(<AuthProvider><Sidebar /></AuthProvider>);

    await screen.findByText(anchor);
    expect(screen.getByRole("button", { name: /테마$/ })).toBeInTheDocument();
  });
});
