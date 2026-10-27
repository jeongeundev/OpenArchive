// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthStatus, Folder, FolderScope } from "@/lib/types";
import DepartmentsPage from "./page";

const push = vi.fn();
const state: { auth: AuthStatus; folders: Folder[]; loading: boolean; error: string | null } = {
  auth: { authenticated: true, username: "kim", is_admin: false }, folders: [], loading: false, error: null,
};
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ auth: state.auth, loading: false }) }));
vi.mock("@/lib/useFolders", () => ({ useFolders: () => ({
  folders: state.folders, loading: state.loading, error: state.error, refresh: vi.fn(),
}) }));

const publicScope: FolderScope = { visibility: "public", users: [], groups: [] };
const bizScope: FolderScope = { visibility: "private", users: [], groups: ["사업팀"] };
function folder(id: string, name: string, parent: string | null, count: number, extra: Partial<Folder> = {}): Folder {
  return { id, name, parent_id: parent, created_by: "alice", document_count: count, scope: publicScope,
    inherited: parent !== null, can_manage: false, can_change_access: false, can_share: false, ...extra };
}

const biz = folder("f-biz", "사업", null, 2, { scope: bizScope, department: { id: "g-biz", name: "사업팀" } });
const rfp = folder("f-rfp", "RFP", "f-biz", 3, { scope: bizScope });
const rfp2026 = folder("f-rfp26", "2026", "f-rfp", 1, { scope: bizScope });
const hr = folder("f-hr", "인사", null, 4, { department: { id: "g-hr", name: "인사팀" } });
const common = folder("f-common", "공용 자료", null, 5);

afterEach(() => {
  state.auth = { authenticated: true, username: "kim", is_admin: false };
  state.folders = [];
  state.loading = false;
  state.error = null;
  push.mockReset();
});

function cardOf(name: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(name) });
}

describe("부서 화면", () => {
  it("보이는 최상위 폴더를 부서 카드와 공용 폴더 카드로 보이고 합산 문서·폴더 수를 단다", () => {
    state.folders = [biz, rfp, rfp2026, hr, common];
    render(<DepartmentsPage />);

    expect(screen.getByRole("heading", { level: 1, name: "부서" })).toBeInTheDocument();
    expect(screen.getByText("부서별 폴더와 열람 범위를 봅니다.")).toBeInTheDocument();
    expect(screen.queryByText("부서 추가")).not.toBeInTheDocument();

    const bizCard = cardOf("사업팀");
    expect(within(bizCard).getByText("부서 폴더")).toBeInTheDocument();
    expect(within(bizCard).getByText("문서 6 · 폴더 2")).toBeInTheDocument();
    expect(within(cardOf("인사팀")).getByText("문서 4 · 폴더 0")).toBeInTheDocument();
    const commonCard = cardOf("공용 자료");
    expect(within(commonCard).getByText("공용 폴더")).toBeInTheDocument();
    expect(within(commonCard).getByText("문서 5 · 폴더 0")).toBeInTheDocument();
  });

  it("내 부서 폴더를 처음 고르고 그 폴더 구조·열람 권한을 보인다", () => {
    state.auth = { ...state.auth, department: { name: "인사팀", folder_id: "f-hr" } };
    state.folders = [biz, rfp, rfp2026, hr, common];
    render(<DepartmentsPage />);

    expect(cardOf("인사팀")).toHaveClass("border-accent", "bg-accent-soft");
    expect(cardOf("사업팀")).not.toHaveClass("border-accent");
    expect(screen.getByRole("heading", { level: 2, name: "인사팀" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "문서 보기" })).toHaveAttribute("href", "/documents?folder=f-hr");
  });

  it("카드를 누르면 하위 폴더 구조와 범위·관리 부서를 보이고 하위 폴더를 누르면 그 폴더 문서로 간다", () => {
    state.folders = [biz, rfp, rfp2026, hr, common];
    render(<DepartmentsPage />);
    fireEvent.click(cardOf("사업팀"));

    expect(screen.getByRole("link", { name: "문서 보기" })).toHaveAttribute("href", "/documents?folder=f-biz");
    const tree = screen.getByRole("region", { name: "폴더 구조" });
    expect(within(tree).getByText("RFP")).toBeInTheDocument();
    expect(within(tree).getByText("4")).toBeInTheDocument();
    expect(within(tree).getByText("2026")).toBeInTheDocument();
    fireEvent.click(within(tree).getByRole("button", { name: /2026/ }));
    expect(push).toHaveBeenCalledWith("/documents?folder=f-rfp26");

    const access = screen.getByRole("region", { name: "열람 권한" });
    expect(within(access).getByText("제한 · 사업팀")).toBeInTheDocument();
    expect(within(access).getByText("관리 부서")).toBeInTheDocument();
    expect(within(access).getAllByText("사업팀").length).toBeGreaterThan(0);
    expect(screen.queryByText("+ 구성원 추가")).not.toBeInTheDocument();
    expect(screen.queryByText("+ 새 폴더")).not.toBeInTheDocument();
  });

  it("부서가 없고 하위 폴더도 없는 공용 폴더는 관리 부서 줄을 빼고, 부서 없으면 첫 카드를 고른다", () => {
    state.folders = [common];
    render(<DepartmentsPage />);

    expect(cardOf("공용 자료")).toHaveClass("border-accent");
    const access = screen.getByRole("region", { name: "열람 권한" });
    expect(within(access).getByText("조직 공개")).toBeInTheDocument();
    expect(within(access).queryByText("관리 부서")).not.toBeInTheDocument();
  });

  it("관리자에게만 그룹 관리 링크를 보인다", () => {
    state.folders = [hr];
    const { unmount } = render(<DepartmentsPage />);
    expect(screen.queryByRole("link", { name: "그룹 관리" })).not.toBeInTheDocument();
    unmount();

    state.auth = { ...state.auth, is_admin: true };
    render(<DepartmentsPage />);
    expect(screen.getByRole("link", { name: "그룹 관리" })).toHaveAttribute("href", "/admin/groups");
  });

  it("볼 수 있는 폴더가 없으면 그렇다고 알리고 빈 카드를 남기지 않는다", () => {
    render(<DepartmentsPage />);
    expect(screen.getByText("볼 수 있는 부서 폴더가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/문서 0/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "문서 보기" })).not.toBeInTheDocument();
  });

  it("폴더를 불러오지 못하면 오류를 보인다", () => {
    state.error = "폴더를 불러오지 못했습니다.";
    render(<DepartmentsPage />);
    expect(screen.getByText("폴더를 불러오지 못했습니다.")).toBeInTheDocument();
    expect(screen.queryByText("볼 수 있는 부서 폴더가 없습니다.")).not.toBeInTheDocument();
  });
});
