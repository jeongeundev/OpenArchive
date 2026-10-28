// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import UsersPage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const admin = { authenticated: true, username: "admin", is_admin: true };
const users = [{
  id: "user-1",
  username: "alice",
  is_admin: false,
  created_at: "2026-08-11T00:00:00Z",
}];
const groups: unknown[] = [];

describe("사용자 관리 화면", () => {
  it("관리자가 목록을 보고 계정을 생성한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups))
      .mockResolvedValueOnce(response({ ...users[0], id: "user-2", username: "bob" }, 201))
      .mockResolvedValueOnce(response([...users, { ...users[0], id: "user-2", username: "bob" }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><UsersPage /></AuthProvider>);
    expect(await screen.findByText("alice")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("초기 비밀번호"), { target: { value: "secret-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
  });

  it("삭제 전에 소유물 이전 흐름을 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups)));

    render(<AuthProvider><UsersPage /></AuthProvider>);

    expect(await screen.findByText(/문서나 폴더를 소유한 사용자는 이전받을 사용자를 고른 뒤 삭제합니다/)).toBeInTheDocument();
  });

  it("확인 후 사용자를 삭제하고 목록을 갱신한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AuthProvider><UsersPage /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user-1",
      expect.objectContaining({ method: "DELETE" }),
    ));
  });

  it("일반 사용자는 관리 내용을 볼 수 없다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><UsersPage /></AuthProvider>);

    expect(await screen.findByText("관리자 권한이 필요합니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "사용자 생성" })).not.toBeInTheDocument();
  });
});

describe("사용자 관리 화면 취소", () => {
  it("화면을 떠나면 진행 중인 목록 조회를 취소한다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(admin));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><UsersPage /></AuthProvider>);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    unmount();

    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    expect(fetchMock.mock.calls[2][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("사용자 관리 화면 — 동작 뒤 조회 취소", () => {
  it("생성 뒤 목록을 다시 받는 중에 화면을 떠나면 그 조회를 취소한다 — 생성 요청은 건드리지 않는다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups))
      .mockResolvedValueOnce(response({ ...users[0], id: "user-2", username: "bob" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><UsersPage /></AuthProvider>);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("초기 비밀번호"), { target: { value: "secret-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    unmount();

    expect(fetchMock.mock.calls[3][1]?.signal).toBeFalsy();
    expect(fetchMock.mock.calls[4][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});


it("409인 행에서 자신과 삭제하는 관리자를 제외한 이전 대상을 골라 삭제한다", async () => {
  const lee = { ...users[0], id: "user-2", username: "lee" };
  const self = { ...users[0], id: "user-0", username: "admin", is_admin: true };
  const fetchMock = vi.fn().mockResolvedValueOnce(response(admin)).mockResolvedValueOnce(response([self, ...users, lee])).mockResolvedValueOnce(response(groups))
    .mockResolvedValueOnce(response({ detail: "소유한 문서나 만든 폴더가 있어 삭제할 수 없습니다." }, 409))
    .mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(response([lee]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AuthProvider><UsersPage /></AuthProvider>);
  await screen.findByText("alice");
  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);
  expect(await screen.findByRole("alert")).toHaveTextContent("소유한 문서나 만든 폴더가 있어 삭제할 수 없습니다.");
  const select = screen.getByLabelText("이전받을 사용자");
  expect(select.querySelector('option[value="alice"]')).toBeNull();
  // 관리자는 이전만 하고 열람을 얻지 않는다 — 자기에게 옮기면 소유자로서 읽게 된다(ADR-061 결정 2).
  expect(select.querySelector('option[value="admin"]')).toBeNull();
  expect(screen.getByRole("button", { name: "이전 후 삭제" })).toBeDisabled();
  fireEvent.change(select, { target: { value: "lee" } });
  fireEvent.click(screen.getByRole("button", { name: "이전 후 삭제" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-1?transfer_to=lee", expect.objectContaining({ method: "DELETE" })));
  await waitFor(() => expect(screen.queryByText("alice")).not.toBeInTheDocument());
});


describe("사용자 관리 화면 — 비밀번호 길이·자기 삭제 (ADR-040 2026-10-12 개정)", () => {
  const self = { ...users[0], id: "user-0", username: "admin", is_admin: true };

  it("비밀번호 칸은 8자 이상을 요구하고 그렇게 안내한다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups)));

    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));

    const input = screen.getByLabelText("초기 비밀번호");
    expect(input).toHaveAttribute("minLength", "8");
    expect(input).toHaveAccessibleDescription("8자 이상");
  });

  it("생성이 400으로 거부되면 서버가 준 이유를 보여준다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups))
      .mockResolvedValueOnce(response({ detail: "비밀번호는 8자 이상이어야 합니다." }, 400)));

    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("초기 비밀번호"), { target: { value: "secret-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("비밀번호는 8자 이상이어야 합니다.");
  });

  it("자기 행에는 삭제 버튼 대신 삭제할 수 없다는 안내와 「나」 배지가 있다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response([self, ...users]))
      .mockResolvedValueOnce(response(groups)));

    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");

    const [ownRow, otherRow] = screen.getAllByRole("row").slice(1);
    expect(within(ownRow).getByText("나")).toBeInTheDocument();
    expect(within(ownRow).getByText("본인 계정은 삭제할 수 없음")).toBeInTheDocument();
    expect(within(ownRow).queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
    expect(within(otherRow).queryByText("나")).not.toBeInTheDocument();
    expect(within(otherRow).getByRole("button", { name: "삭제" })).toBeEnabled();
  });

  it("삭제가 400으로 거부되면 서버가 준 이유를 보이고 이전 선택은 열지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response(groups))
      .mockResolvedValueOnce(response({ detail: "자기 계정은 삭제할 수 없습니다." }, 400)));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AuthProvider><UsersPage /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("자기 계정은 삭제할 수 없습니다.");
    expect(screen.queryByLabelText("이전받을 사용자")).not.toBeInTheDocument();
  });
});

describe("사용자 관리 화면 — 소속 부서 (D17)", () => {
  const strategy = { id: "group-1", name: "IT전략실", created_at: "2026-10-27T00:00:00Z", members: [] };
  const sales = { id: "group-2", name: "영업팀", created_at: "2026-10-27T00:00:00Z", members: [] };

  function routed(handlers: { users: () => unknown; groups?: unknown[] }) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return response(admin);
      if (url === "/api/admin/groups") return response(handlers.groups ?? [strategy, sales]);
      if (url === "/api/admin/users" && (init?.method ?? "GET") === "GET") return response(handlers.users());
      if (url === "/api/admin/users") return response({ ...users[0], id: "user-2", username: "bob" }, 201);
      if (url.endsWith("/department")) return new Response(null, { status: 204 });
      return response({ detail: "없음" }, 404);
    });
  }

  function createBody(fetchMock: ReturnType<typeof routed>): Record<string, unknown> {
    const call = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/admin/users" && init?.method === "POST");
    return JSON.parse(String(call?.[1]?.body));
  }

  async function fillAndCreate(): Promise<void> {
    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("초기 비밀번호"), { target: { value: "secret-pw" } });
  }

  it("생성 폼의 소속 부서는 「없음」과 그룹 목록을 고르게 하고, 고른 부서를 요청에 싣는다", async () => {
    const fetchMock = routed({ users: () => users });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthProvider><UsersPage /></AuthProvider>);
    await fillAndCreate();

    const select = screen.getByRole("combobox", { name: "소속 부서" });
    expect([...select.querySelectorAll("option")].map(option => option.textContent)).toEqual(["없음", "IT전략실", "영업팀"]);
    expect(select).toHaveValue("");
    fireEvent.change(select, { target: { value: "group-1" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    await waitFor(() => expect(createBody(fetchMock)).toEqual({ username: "bob", password: "secret-pw", is_admin: false, department_group_id: "group-1" }));
    await waitFor(() => expect(select).toHaveValue(""));
  });

  it("소속 부서가 「없음」이면 요청에 부서 필드를 싣지 않는다", async () => {
    const fetchMock = routed({ users: () => users });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthProvider><UsersPage /></AuthProvider>);
    await fillAndCreate();
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    await waitFor(() => expect(createBody(fetchMock)).toEqual({ username: "bob", password: "secret-pw", is_admin: false }));
  });

  it("목록에 소속 부서 칸이 있고, 바꾸면 부서 변경을 요청한 뒤 목록을 다시 받는다", async () => {
    let current = [{ ...users[0], department: null }, { ...users[0], id: "user-3", username: "kim", department: { id: "group-1", name: "IT전략실" } }];
    const fetchMock = routed({ users: () => current });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");

    expect(screen.getByRole("columnheader", { name: "소속 부서" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "alice 소속 부서" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "kim 소속 부서" })).toHaveValue("group-1");

    current = [{ ...current[0], department: { id: "group-2", name: "영업팀" } }, current[1]];
    fireEvent.change(screen.getByRole("combobox", { name: "alice 소속 부서" }), { target: { value: "group-2" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-1/department", expect.objectContaining({ method: "PUT", body: JSON.stringify({ group_id: "group-2" }) })));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url, init]) => String(url) === "/api/admin/users" && (init?.method ?? "GET") === "GET")).toHaveLength(2));
    expect(screen.getByRole("combobox", { name: "alice 소속 부서" })).toHaveValue("group-2");

    fireEvent.change(screen.getByRole("combobox", { name: "kim 소속 부서" }), { target: { value: "" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-3/department", expect.objectContaining({ method: "PUT", body: JSON.stringify({ group_id: null }) })));
  });

  it("그룹이 하나도 없으면 그룹 관리에서 부서를 먼저 만들라고 안내한다", async () => {
    vi.stubGlobal("fetch", routed({ users: () => users, groups: [] }));
    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));

    expect(await screen.findByText(/먼저 그룹 관리에서 부서\(그룹\)를 만드세요/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "그룹 관리" })).toHaveAttribute("href", "/admin/groups");
  });
});

describe("사용자 관리 화면 — 시안 (m44 step 7)", () => {
  const strategy = { id: "group-1", name: "IT전략실", created_at: "2026-10-27T00:00:00Z", members: [] };
  const roster = [
    { id: "user-0", username: "admin", is_admin: true, created_at: "2026-08-11T00:00:00Z", department: { id: "group-1", name: "IT전략실" }, owned_documents: 0, created_folders: 0 },
    { id: "user-1", username: "alice", is_admin: false, created_at: "2026-08-11T00:00:00Z", department: { id: "group-1", name: "IT전략실" }, owned_documents: 4, created_folders: 0 },
    { id: "user-2", username: "bob", is_admin: false, created_at: "2026-08-11T00:00:00Z", department: null, owned_documents: 0, created_folders: 0 },
    { id: "user-3", username: "kim", is_admin: false, created_at: "2026-08-11T00:00:00Z", department: { id: "group-1", name: "IT전략실" }, owned_documents: 0, created_folders: 2 },
  ];

  function routed(list: () => unknown[] = () => roster) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/auth/me") return response(admin);
      if (url === "/api/admin/groups") return response([strategy]);
      if (url === "/api/admin/users" && method === "GET") return response(list());
      if (url === "/api/admin/users") return response({ id: "user-9", username: "lee", is_admin: true, created_at: "2026-10-28T00:00:00Z" }, 201);
      if (method === "DELETE") return new Response(null, { status: 204 });
      return response({ detail: "없음" }, 404);
    });
  }

  function cardValue(label: string): string | null | undefined {
    return screen.getByText(label).nextElementSibling?.textContent;
  }

  async function renderPage(fetchMock = routed()): Promise<typeof fetchMock> {
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthProvider><UsersPage /></AuthProvider>);
    await screen.findByText("alice");
    return fetchMock;
  }

  it("머리에 관리 표지·제목·부제가 있다", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "사용자 관리" })).toBeInTheDocument();
    expect(screen.getByText("계정을 만들고, 소속 부서와 권한을 관리합니다.")).toBeInTheDocument();
  });

  it("「사용자 추가」로 새 사용자 칸을 열고 「취소」로 닫는다", async () => {
    await renderPage();
    expect(screen.queryByRole("button", { name: "사용자 생성" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    expect(screen.getByText("계정을 만들면 감사 로그에 기록됩니다")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "사용자명" })).toHaveAttribute("placeholder", "예) 홍길동");
    expect(screen.getByLabelText("초기 비밀번호")).toHaveAttribute("placeholder", "8자 이상");
    expect(screen.getByRole("radio", { name: /^일반 사용자/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /^관리자/ })).not.toBeChecked();

    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "half" } });
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("button", { name: "사용자 생성" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    expect(screen.getByRole("textbox", { name: "사용자명" })).toHaveValue("");
  });

  it("권한 라디오가 is_admin으로, 고른 소속 부서와 함께 생성 요청에 실린다", async () => {
    const fetchMock = await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "+ 사용자 추가" }));
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "lee" } });
    fireEvent.change(screen.getByLabelText("초기 비밀번호"), { target: { value: "secret-pw" } });
    fireEvent.change(screen.getByRole("combobox", { name: "소속 부서" }), { target: { value: "group-1" } });
    fireEvent.click(screen.getByRole("radio", { name: /^관리자/ }));
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/admin/users" && init?.method === "POST");
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ username: "lee", password: "secret-pw", is_admin: true, department_group_id: "group-1" });
    });
  });

  it("요약 카드가 전체·관리자·일반 사용자·부서 미지정 수를 보인다", async () => {
    await renderPage();
    expect(cardValue("전체 계정")).toBe("4");
    expect(cardValue("부서 미지정")).toBe("1");
    expect(screen.getAllByText("관리자").length).toBeGreaterThan(0);
    const cards = screen.getByText("전체 계정").parentElement?.parentElement as HTMLElement;
    expect(within(cards).getByText("관리자").nextElementSibling?.textContent).toBe("1");
    expect(within(cards).getByText("일반 사용자").nextElementSibling?.textContent).toBe("3");
  });

  it("이름 검색과 권한 칩으로 목록을 거른다", async () => {
    await renderPage();
    expect(screen.getByRole("button", { name: "전체 4" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "관리자 1" }));
    expect(screen.queryByText("alice")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "admin 소속 부서" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "일반 사용자 3" }));
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "admin 소속 부서" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "전체 4" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "이름으로 찾기" }), { target: { value: "KI" } });
    expect(screen.getByText("kim")).toBeInTheDocument();
    expect(screen.queryByText("alice")).not.toBeInTheDocument();
    expect(screen.queryByText("bob")).not.toBeInTheDocument();
  });

  it("권한 배지와 소유 문서 수를 줄마다 보인다", async () => {
    await renderPage();
    const aliceRow = screen.getByText("alice").closest("tr") as HTMLElement;
    const adminRow = screen.getByRole("combobox", { name: "admin 소속 부서" }).closest("tr") as HTMLElement;
    expect(within(aliceRow).getByText("4건")).toBeInTheDocument();
    expect(within(aliceRow).getByText("일반 사용자")).toBeInTheDocument();
    expect(within(adminRow).getByText("0건")).toBeInTheDocument();
    expect(within(adminRow).getByText("관리자")).toHaveClass("bg-accent-soft", "text-accent");
  });

  it("소유가 없으면 확인 뒤 바로 삭제한다", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = await renderPage();
    const bobRow = screen.getByText("bob").closest("tr") as HTMLElement;
    fireEvent.click(within(bobRow).getByRole("button", { name: "삭제" }));

    expect(confirm).toHaveBeenCalledWith("bob 사용자를 삭제하시겠습니까?");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-2", expect.objectContaining({ method: "DELETE" })));
    confirm.mockRestore();
  });

  it("소유 문서나 만든 폴더가 있으면 삭제 전에 이전받을 사용자를 고르고 이전 후 삭제한다", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = await renderPage();
    const kimRow = screen.getByText("kim").closest("tr") as HTMLElement;
    fireEvent.click(within(kimRow).getByRole("button", { name: "삭제" }));

    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    const select = screen.getByLabelText("이전받을 사용자");
    expect([...select.querySelectorAll("option")].map(option => option.getAttribute("value"))).toEqual(["", "alice", "bob"]);
    expect(screen.getByText(/이전해도 열람 범위는 바뀌지 않습니다/)).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: "이전 후 삭제" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("kim 사용자의 문서와 폴더를 bob 사용자에게 이전한 뒤 삭제하시겠습니까?"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-3?transfer_to=bob", expect.objectContaining({ method: "DELETE" })));
    confirm.mockRestore();
  });

  it("아래 안내 띠가 이전 흐름과 소속 부서 변경의 효과를 알린다", async () => {
    await renderPage();
    expect(screen.getByText("문서나 폴더를 소유한 사용자는 이전받을 사용자를 고른 뒤 삭제합니다. 소속 부서를 바꾸면 그 부서 폴더의 열람 범위가 바로 적용됩니다.")).toBeInTheDocument();
  });
});
