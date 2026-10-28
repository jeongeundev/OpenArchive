// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import type { AuthStatus, Department, Folder, FolderScope } from "@/lib/types";
import DepartmentsPage from "./page";

const push = vi.fn();
const refresh = vi.fn();
const state: { auth: AuthStatus; folders: Folder[]; loading: boolean; error: string | null } = {
  auth: { authenticated: true, username: "kim", is_admin: false }, folders: [], loading: false, error: null,
};
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ auth: state.auth, loading: false }) }));
vi.mock("@/lib/useFolders", () => ({ useFolders: () => ({
  folders: state.folders, loading: state.loading, error: state.error, refresh,
}) }));
vi.mock("@/lib/api", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  listDepartments: vi.fn(), updateDepartment: vi.fn(), addDepartmentMember: vi.fn(), removeDepartmentMember: vi.fn(),
  setDepartmentManager: vi.fn(), listPrincipals: vi.fn(), createGroup: vi.fn(), createFolder: vi.fn(),
  setFolderAccess: vi.fn(), setFolderDepartment: vi.fn(),
  addGroupMember: vi.fn(), removeGroupMember: vi.fn(),
}));

const publicScope: FolderScope = { visibility: "public", users: [], groups: [] };
const bizScope: FolderScope = { visibility: "private", users: [], groups: ["사업팀"] };
function folder(id: string, name: string, parent: string | null, count: number, extra: Partial<Folder> = {}): Folder {
  return { id, name, parent_id: parent, created_by: "alice", document_count: count, scope: publicScope,
    inherited: parent !== null, can_manage: false, can_change_access: false, can_share: false, ...extra };
}
function department(id: string, name: string, folderId: string | null, extra: Partial<Department> = {}): Department {
  return { id, name, description: "", folder_id: folderId, members: [], granted_document_count: 0, can_manage: false, ...extra };
}

const biz = folder("f-biz", "사업", null, 2, { scope: bizScope, department: { id: "g-biz", name: "사업팀" } });
const rfp = folder("f-rfp", "RFP", "f-biz", 3, { scope: bizScope });
const rfp2026 = folder("f-rfp26", "2026", "f-rfp", 1, { scope: bizScope });
const hr = folder("f-hr", "인사", null, 4, { department: { id: "g-hr", name: "인사팀" } });
const common = folder("f-common", "공용 자료", null, 5);
const restricted = folder("f-secret", "임원 자료", null, 1, { scope: { visibility: "private", users: ["lee"], groups: [] } });

const bizDept = department("g-biz", "사업팀", "f-biz", {
  description: "사업 제안과 RFP를 맡는다.", granted_document_count: 7,
  members: [{ username: "kim", manager: true, primary: true }, { username: "park", manager: false, primary: true }],
});
const hrDept = department("g-hr", "인사팀", "f-hr", { members: [{ username: "choi", manager: false, primary: true }] });
const opsDept = department("g-ops", "운영팀", null, { members: [{ username: "kim", manager: false, primary: false }] });

beforeEach(() => {
  vi.mocked(api.listDepartments).mockResolvedValue([bizDept, hrDept]);
  vi.mocked(api.listPrincipals).mockResolvedValue({ users: ["kim", "park", "lee", "choi"], groups: [] });
  for (const fn of [api.updateDepartment, api.addDepartmentMember, api.removeDepartmentMember, api.setDepartmentManager]) {
    vi.mocked(fn).mockResolvedValue(undefined);
  }
});

afterEach(() => {
  state.auth = { authenticated: true, username: "kim", is_admin: false };
  state.folders = [];
  state.loading = false;
  state.error = null;
  push.mockReset();
  refresh.mockReset();
  vi.clearAllMocks();
});

function cardOf(name: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(name) });
}

async function renderPage(): Promise<void> {
  render(<DepartmentsPage />);
  await waitFor(() => expect(api.listDepartments).toHaveBeenCalled());
  await act(async () => {});
}

function manageable(): void {
  vi.mocked(api.listDepartments).mockResolvedValue([{ ...bizDept, can_manage: true }, hrDept]);
}

describe("부서 화면", () => {
  it("부서와 부서가 아닌 최상위 폴더를 카드로 보이고 합산 문서·폴더 수와 구성원 수를 단다", async () => {
    state.folders = [biz, rfp, rfp2026, hr, common];
    await renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "부서" })).toBeInTheDocument();
    expect(screen.getByText("부서별 폴더, 구성원, 기본 열람 범위를 관리합니다.")).toBeInTheDocument();
    expect(screen.queryByText("부서 추가")).not.toBeInTheDocument();

    const bizCard = cardOf("사업팀");
    expect(within(bizCard).getByText("부서 폴더")).toBeInTheDocument();
    expect(within(bizCard).getByTestId("department-counts")).toHaveTextContent("문서 6 폴더 2 구성원 2");
    expect(within(cardOf("인사팀")).getByTestId("department-counts")).toHaveTextContent("문서 4 폴더 0 구성원 1");
    const commonCard = cardOf("공용 자료");
    expect(within(commonCard).getByText("공용 폴더")).toBeInTheDocument();
    expect(within(commonCard).getByTestId("department-counts")).toHaveTextContent("문서 5 폴더 0");
    expect(within(commonCard).getByTestId("department-counts")).not.toHaveTextContent("구성원");
    expect(screen.queryByRole("button", { name: /^사업(?!팀)/ })).toBeNull();
  });

  it("볼 수 있는 폴더가 없는 부서는 「폴더 없음」 칩을 달고 문서 보기를 두지 않는다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([opsDept]);
    await renderPage();

    expect(within(cardOf("운영팀")).getByText("폴더 없음")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "문서 보기" })).not.toBeInTheDocument();
    const access = screen.getByRole("region", { name: "열람 권한" });
    expect(within(access).getByText("기본 열람 범위").nextSibling).toHaveTextContent("—");
  });

  it("내 소속 부서를 먼저 놓고 처음 고른다", async () => {
    state.auth = { ...state.auth, department: { name: "인사팀", folder_id: "f-hr" } };
    state.folders = [biz, rfp, rfp2026, hr, common];
    await renderPage();

    const ordered = screen.getAllByRole("button").filter(button => button.hasAttribute("aria-pressed"));
    expect(ordered.map(button => button.textContent)).toEqual([
      expect.stringContaining("인사팀"), expect.stringContaining("공용 자료"), expect.stringContaining("사업팀"),
    ]);
    expect(cardOf("인사팀")).toHaveClass("border-accent", "bg-accent-soft");
    expect(cardOf("사업팀")).not.toHaveClass("border-accent");
    expect(screen.getByRole("heading", { level: 2, name: "인사팀" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "문서 보기" })).toHaveAttribute("href", "/documents?folder=f-hr");
  });

  it("카드를 누르면 설명·하위 폴더 구조·구성원·열람 권한을 보이고 하위 폴더를 누르면 그 폴더 문서로 간다", async () => {
    state.folders = [biz, rfp, rfp2026, hr, common];
    await renderPage();
    fireEvent.click(cardOf("사업팀"));

    expect(screen.getByRole("heading", { level: 2, name: "사업팀" })).toBeInTheDocument();
    expect(screen.getByText("사업 제안과 RFP를 맡는다.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "문서 보기" })).toHaveAttribute("href", "/documents?folder=f-biz");
    const tree = screen.getByRole("region", { name: "폴더 구조" });
    expect(within(tree).getByText("RFP")).toBeInTheDocument();
    expect(within(tree).getByText("4")).toBeInTheDocument();
    expect(within(tree).getByText("2026")).toBeInTheDocument();
    fireEvent.click(within(tree).getByRole("button", { name: /2026/ }));
    expect(push).toHaveBeenCalledWith("/documents?folder=f-rfp26");

    const members = screen.getByRole("region", { name: "구성원" });
    expect(within(members).getByText("kim")).toBeInTheDocument();
    expect(within(members).getByText("부서 관리자")).toBeInTheDocument();
    expect(within(members).getByText("park").closest("li")).toHaveTextContent("구성원");
    expect(within(members).getByText("park").closest("li")).not.toHaveTextContent("부서 관리자");

    const access = screen.getByRole("region", { name: "열람 권한" });
    expect(within(access).getByText("제한 · 사업팀")).toBeInTheDocument();
    expect(within(access).getByText("관리 부서").nextSibling).toHaveTextContent("사업팀");
    expect(within(access).getByText("개별 부여 문서").nextSibling).toHaveTextContent("7건");
  });

  it("관리할 수 없는 부서에는 설정·구성원 추가·빼기·관리자 지정이 없다", async () => {
    state.folders = [biz, rfp];
    await renderPage();

    expect(screen.queryByRole("button", { name: "설정" })).not.toBeInTheDocument();
    expect(screen.queryByText("+ 구성원 추가")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /빼기/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /관리자 (지정|해제)/ })).not.toBeInTheDocument();
    expect(screen.queryByText("설명이 없습니다.")).not.toBeInTheDocument();
  });

  it("관리할 수 있으면 구성원을 부서 API로 추가하고 부서·폴더 목록을 다시 읽는다", async () => {
    manageable();
    state.folders = [biz, rfp];
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "+ 구성원 추가" }));
    const select = await screen.findByRole("combobox", { name: "추가할 사용자" });
    expect(within(select).queryByRole("option", { name: "kim" })).toBeNull();
    expect(within(select).queryByRole("option", { name: "park" })).toBeNull();
    fireEvent.change(select, { target: { value: "lee" } });
    vi.mocked(api.listDepartments).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "추가" }));

    await waitFor(() => expect(api.addDepartmentMember).toHaveBeenCalledWith("g-biz", "lee"));
    await waitFor(() => expect(api.listDepartments).toHaveBeenCalled());
    expect(refresh).toHaveBeenCalled();
    expect(api.addGroupMember).not.toHaveBeenCalled();
  });

  it("구성원 추가가 거부되면 서버 문구를 알린다", async () => {
    manageable();
    vi.mocked(api.addDepartmentMember).mockRejectedValue(new api.ApiError(403, "부서 관리자만 바꿉니다."));
    state.folders = [biz];
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "+ 구성원 추가" }));
    fireEvent.change(await screen.findByRole("combobox", { name: "추가할 사용자" }), { target: { value: "lee" } });
    fireEvent.click(screen.getByRole("button", { name: "추가" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("부서 관리자만 바꿉니다.");
  });

  it("관리자 지정·해제와 빼기(확인 뒤)를 부서 API로 한다", async () => {
    manageable();
    state.folders = [biz];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "park 관리자 지정" }));
    await waitFor(() => expect(api.setDepartmentManager).toHaveBeenCalledWith("g-biz", "park", true));
    fireEvent.click(screen.getByRole("button", { name: "kim 관리자 해제" }));
    await waitFor(() => expect(api.setDepartmentManager).toHaveBeenCalledWith("g-biz", "kim", false));

    fireEvent.click(screen.getByRole("button", { name: "park 빼기" }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.removeDepartmentMember).toHaveBeenCalledWith("g-biz", "park"));
    expect(api.removeGroupMember).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("빼기를 취소하면 부르지 않는다", async () => {
    manageable();
    state.folders = [biz];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "park 빼기" }));
    expect(api.removeDepartmentMember).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("설정에서 설명을 저장한다 — 설명이 없으면 관리 가능자에게 그렇다고 보인다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([{ ...bizDept, description: "", can_manage: true }]);
    state.folders = [biz];
    await renderPage();

    expect(screen.getByText("설명이 없습니다.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "설정" }));
    const input = screen.getByRole("textbox", { name: "부서 설명" });
    expect(input).toHaveAttribute("maxLength", "200");
    fireEvent.change(input, { target: { value: "사업 제안 담당" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(api.updateDepartment).toHaveBeenCalledWith("g-biz", "사업 제안 담당"));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "부서 설명" })).not.toBeInTheDocument());
  });

  it("관리자에게만 「부서 추가」를 보이고 그룹을 만든 뒤 목록을 다시 읽는다", async () => {
    state.folders = [hr];
    const { unmount } = render(<DepartmentsPage />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "부서 추가" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "그룹 관리" })).not.toBeInTheDocument();
    unmount();

    state.auth = { ...state.auth, is_admin: true };
    vi.mocked(api.createGroup).mockResolvedValue({ id: "g-new", name: "법무팀", created_at: "2026-10-28" } as never);
    await renderPage();
    expect(screen.getByRole("link", { name: "그룹 관리" })).toHaveAttribute("href", "/admin/groups");
    fireEvent.click(screen.getByRole("button", { name: "부서 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "부서 이름" }), { target: { value: "법무팀" } });
    vi.mocked(api.listDepartments).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "추가" }));
    await waitFor(() => expect(api.createGroup).toHaveBeenCalledWith("법무팀"));
    await waitFor(() => expect(api.listDepartments).toHaveBeenCalled());
  });

  it("부서 폴더가 있으면 「+ 새 폴더」로 그 아래 하위 폴더를 만든다", async () => {
    vi.mocked(api.createFolder).mockResolvedValue(folder("f-new", "제안서", "f-biz", 0));
    state.folders = [biz, rfp];
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "+ 새 폴더" }));
    fireEvent.change(screen.getByRole("textbox", { name: "새 폴더 이름" }), { target: { value: "제안서" } });
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));
    await waitFor(() => expect(api.createFolder).toHaveBeenCalledWith({ name: "제안서", parentId: "f-biz" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("부서 폴더가 없고 관리할 수 있는 구성원이면 부서 폴더를 만들고 제한 범위·부서 지정을 순서대로 건다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([{ ...opsDept, can_manage: true }]);
    vi.mocked(api.createFolder).mockResolvedValue(folder("f-ops", "운영팀", null, 0));
    vi.mocked(api.setFolderAccess).mockResolvedValue({ visibility: "private", users: [], groups: ["운영팀"] });
    vi.mocked(api.setFolderDepartment).mockResolvedValue(folder("f-ops", "운영팀", null, 0));
    await renderPage();

    expect(screen.queryByRole("button", { name: "+ 새 폴더" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ 부서 폴더 만들기" }));
    await waitFor(() => expect(api.setFolderDepartment).toHaveBeenCalledWith("f-ops", "운영팀"));
    expect(api.createFolder).toHaveBeenCalledWith({ name: "운영팀" });
    expect(api.setFolderAccess).toHaveBeenCalledWith("f-ops", { visibility: "private", users: [], groups: ["운영팀"] });
    const order = [api.createFolder, api.setFolderAccess, api.setFolderDepartment]
      .map(fn => vi.mocked(fn).mock.invocationCallOrder[0]);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(refresh).toHaveBeenCalled();
  });

  it("부서 폴더 만들기가 중간에 실패하면 서버 문구를 보이고 부서 지정은 하지 않는다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([{ ...opsDept, can_manage: true }]);
    vi.mocked(api.createFolder).mockResolvedValue(folder("f-ops", "운영팀", null, 0));
    vi.mocked(api.setFolderAccess).mockRejectedValue(new api.ApiError(400, "범위를 바꿀 수 없습니다."));
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "+ 부서 폴더 만들기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("범위를 바꿀 수 없습니다.");
    expect(api.setFolderDepartment).not.toHaveBeenCalled();
  });

  it("구성원이 아니면 관리할 수 있어도 부서 폴더 만들기를 두지 않는다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([{ ...opsDept, members: [], can_manage: true }]);
    await renderPage();
    expect(screen.queryByRole("button", { name: "+ 부서 폴더 만들기" })).not.toBeInTheDocument();
  });

  it("공용 폴더 카드는 구성원 대신 열람 안내를 보이고 관리 부서는 「—」, 개별 부여 문서 줄이 없다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([]);
    state.folders = [common, restricted];
    await renderPage();

    expect(cardOf("공용 자료")).toHaveClass("border-accent");
    expect(screen.getByText("조직 공개 폴더 — 로그인한 모든 사용자가 봅니다.")).toBeInTheDocument();
    const access = screen.getByRole("region", { name: "열람 권한" });
    expect(within(access).getByText("조직 공개")).toBeInTheDocument();
    expect(within(access).getByText("관리 부서").nextSibling).toHaveTextContent("—");
    expect(within(access).queryByText("개별 부여 문서")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "구성원" })).not.toBeInTheDocument();

    fireEvent.click(cardOf("임원 자료"));
    expect(screen.getByText("제한 폴더 — 부여된 사용자·그룹만 봅니다.")).toBeInTheDocument();
  });

  it("부모가 보이지 않는 하위 폴더는 최상위 카드로 올리지 않는다 — 홈과 같은 parent_id 기준", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([hrDept]);
    state.folders = [hr, folder("f-rfp-only", "RFP", "f-biz", 3, { scope: bizScope })];
    await renderPage();

    expect(cardOf("인사팀")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /RFP/ })).toBeNull();
    expect(screen.queryByText("공용 폴더")).toBeNull();
  });

  it("볼 수 있는 부서·폴더가 없으면 그렇다고 알리고 빈 카드를 남기지 않는다", async () => {
    vi.mocked(api.listDepartments).mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("볼 수 있는 부서 폴더가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/문서 0/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "문서 보기" })).not.toBeInTheDocument();
  });

  it("폴더를 불러오지 못하면 오류를 보인다", async () => {
    state.error = "폴더를 불러오지 못했습니다.";
    await renderPage();
    expect(screen.getByText("폴더를 불러오지 못했습니다.")).toBeInTheDocument();
    expect(screen.queryByText("볼 수 있는 부서 폴더가 없습니다.")).not.toBeInTheDocument();
  });

  it("부서를 불러오지 못하면 서버 문구를 보인다", async () => {
    vi.mocked(api.listDepartments).mockRejectedValue(new api.ApiError(500, "부서를 불러오지 못했습니다."));
    await renderPage();
    expect(screen.getByText("부서를 불러오지 못했습니다.")).toBeInTheDocument();
  });
});
