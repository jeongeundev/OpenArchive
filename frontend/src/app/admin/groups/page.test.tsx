import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import GroupsPage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const admin = { authenticated: true, username: "admin", is_admin: true };
const users = [
  { id: "u-1", username: "alice", is_admin: false, created_at: "2026-10-01T00:00:00Z" },
  { id: "u-2", username: "bob", is_admin: false, created_at: "2026-10-01T00:00:00Z" },
];
const hr = { id: "g-1", name: "인사팀", created_at: "2026-10-01T00:00:00Z", members: ["alice"] };

type Route = (url: string, init?: RequestInit) => Response | undefined;

/** 경로와 메서드로 응답을 고른다 — 화면이 목록 두 개를 함께 받아 호출 순서가 고정되지 않는다. */
function routedFetch(groupsSequence: unknown[], extra: Route = () => undefined) {
  let groupCalls = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const custom = extra(url, init);
    if (custom !== undefined) return custom;
    if (url === "/api/auth/me") return response(admin);
    if (url === "/api/admin/users") return response(users);
    if (url === "/api/admin/groups" && method === "GET") {
      const body = groupsSequence[Math.min(groupCalls, groupsSequence.length - 1)];
      groupCalls += 1;
      return response(body);
    }
    return new Response(null, { status: 204 });
  });
}

describe("그룹 관리 화면", () => {
  it("그룹을 이름과 구성원으로 보여 준다", async () => {
    vi.stubGlobal("fetch", routedFetch([[hr]]));

    render(<AuthProvider><GroupsPage /></AuthProvider>);

    const card = await screen.findByRole("region", { name: "인사팀" });
    expect(within(card).getByText("alice")).toBeInTheDocument();
  });

  it("그룹이 없으면 빈 상태를 알린다", async () => {
    vi.stubGlobal("fetch", routedFetch([[]]));

    render(<AuthProvider><GroupsPage /></AuthProvider>);

    expect(await screen.findByText("아직 그룹이 없습니다.")).toBeInTheDocument();
  });

  it("구성원 변경이 열람을 바꾼다는 것과 직접 부여를 안내한다", async () => {
    vi.stubGlobal("fetch", routedFetch([[]]));

    render(<AuthProvider><GroupsPage /></AuthProvider>);

    expect(await screen.findByText(/그룹 구성원을 바꾸면 그 그룹에 부여된 문서의 열람이 바뀝니다/)).toBeInTheDocument();
    expect(screen.getByText(/관리자도 보면 안 되는 문서는 사용자에게 직접 부여하세요/)).toBeInTheDocument();
  });

  it("이름을 넣고 그룹을 만들면 목록을 다시 받는다", async () => {
    const fetchMock = routedFetch([[], [{ ...hr, id: "g-2", name: "재무팀", members: [] }]], (url, init) =>
      url === "/api/admin/groups" && init?.method === "POST"
        ? response({ id: "g-2", name: "재무팀", created_at: "2026-10-01T00:00:00Z", members: [] }, 201)
        : undefined);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><GroupsPage /></AuthProvider>);
    await screen.findByText("아직 그룹이 없습니다.");
    fireEvent.change(screen.getByRole("textbox", { name: "그룹 이름" }), { target: { value: "재무팀" } });
    fireEvent.click(screen.getByRole("button", { name: "그룹 생성" }));

    expect(await screen.findByRole("region", { name: "재무팀" })).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[1]?.body).toBe(JSON.stringify({ name: "재무팀" }));
  });

  it("이미 있는 이름이면 서버가 알려 준 이유를 보여 준다", async () => {
    vi.stubGlobal("fetch", routedFetch([[hr]], (url, init) =>
      url === "/api/admin/groups" && init?.method === "POST"
        ? response({ detail: "이미 있는 그룹 이름입니다." }, 409)
        : undefined));

    render(<AuthProvider><GroupsPage /></AuthProvider>);
    await screen.findByRole("region", { name: "인사팀" });
    fireEvent.change(screen.getByRole("textbox", { name: "그룹 이름" }), { target: { value: "인사팀" } });
    fireEvent.click(screen.getByRole("button", { name: "그룹 생성" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("이미 있는 그룹 이름입니다.");
  });

  it("아직 구성원이 아닌 사용자만 골라 추가한다", async () => {
    const fetchMock = routedFetch([[hr], [{ ...hr, members: ["alice", "bob"] }]]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><GroupsPage /></AuthProvider>);
    const card = await screen.findByRole("region", { name: "인사팀" });
    const select = within(card).getByRole("combobox", { name: "추가할 사용자" });
    const options = within(select).getAllByRole("option").map((option) => option.textContent);
    expect(options).toContain("bob");
    expect(options).not.toContain("alice");

    fireEvent.change(select, { target: { value: "bob" } });
    fireEvent.click(within(card).getByRole("button", { name: "구성원 추가" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/groups/g-1/members/bob",
      expect.objectContaining({ method: "PUT" }),
    ));
    expect(await within(card).findByText("bob")).toBeInTheDocument();
  });

  it("구성원을 제거한다", async () => {
    const fetchMock = routedFetch([[hr], [{ ...hr, members: [] }]]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><GroupsPage /></AuthProvider>);
    const card = await screen.findByRole("region", { name: "인사팀" });
    fireEvent.click(within(card).getByRole("button", { name: "alice 제거" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/groups/g-1/members/alice",
      expect.objectContaining({ method: "DELETE" }),
    ));
  });

  it("삭제 확인에서 부여된 문서가 구성원에게 보이지 않게 됨을 알리고, 확인해야 삭제한다", async () => {
    const fetchMock = routedFetch([[hr], []]);
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

    render(<AuthProvider><GroupsPage /></AuthProvider>);
    const card = await screen.findByRole("region", { name: "인사팀" });
    fireEvent.click(within(card).getByRole("button", { name: "그룹 삭제" }));
    expect(confirm.mock.calls[0][0]).toMatch(/이 그룹에 부여된 문서는 구성원에게 더 이상 보이지 않습니다/);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    fireEvent.click(within(card).getByRole("button", { name: "그룹 삭제" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/groups/g-1",
      expect.objectContaining({ method: "DELETE" }),
    ));
    expect(await screen.findByText("아직 그룹이 없습니다.")).toBeInTheDocument();
  });

  it("일반 사용자는 관리 내용을 볼 수 없다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: true,
      username: "alice",
      is_admin: false,
    })));

    render(<AuthProvider><GroupsPage /></AuthProvider>);

    expect(await screen.findByText("관리자 권한이 필요합니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "그룹 생성" })).not.toBeInTheDocument();
  });
});
