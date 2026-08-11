import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("사용자 관리 화면", () => {
  it("관리자가 목록을 보고 계정을 생성한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response({ ...users[0], id: "user-2", username: "bob" }, 201))
      .mockResolvedValueOnce(response([...users, { ...users[0], id: "user-2", username: "bob" }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><UsersPage /></AuthProvider>);
    expect(await screen.findByText("alice")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  });

  it("삭제 전에 소유 문서를 먼저 삭제해야 함을 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users)));

    render(<AuthProvider><UsersPage /></AuthProvider>);

    expect(await screen.findByText(/소유 문서가 있는 사용자는 삭제할 수 없습니다/)).toBeInTheDocument();
  });

  it("확인 후 사용자를 삭제하고 목록을 갱신한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
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
