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

  it("삭제 전에 소유물 이전 흐름을 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users)));

    render(<AuthProvider><UsersPage /></AuthProvider>);

    expect(await screen.findByText(/문서나 폴더를 소유한 사용자는 이전받을 사용자를 고른 뒤 삭제합니다/)).toBeInTheDocument();
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

describe("사용자 관리 화면 취소", () => {
  it("화면을 떠나면 진행 중인 목록 조회를 취소한다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(admin));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><UsersPage /></AuthProvider>);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    unmount();

    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("사용자 관리 화면 — 동작 뒤 조회 취소", () => {
  it("생성 뒤 목록을 다시 받는 중에 화면을 떠나면 그 조회를 취소한다 — 생성 요청은 건드리지 않는다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(admin))
      .mockResolvedValueOnce(response(users))
      .mockResolvedValueOnce(response({ ...users[0], id: "user-2", username: "bob" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><UsersPage /></AuthProvider>);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "사용자명" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 생성" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    unmount();

    expect(fetchMock.mock.calls[2][1]?.signal).toBeFalsy();
    expect(fetchMock.mock.calls[3][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});


it("409인 행에서 자신을 제외한 이전 대상을 골라 삭제한다", async () => {
  const lee = { ...users[0], id: "user-2", username: "lee" };
  const fetchMock = vi.fn().mockResolvedValueOnce(response(admin)).mockResolvedValueOnce(response([...users, lee]))
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
  expect(screen.getByRole("button", { name: "이전 후 삭제" })).toBeDisabled();
  fireEvent.change(select, { target: { value: "lee" } });
  fireEvent.click(screen.getByRole("button", { name: "이전 후 삭제" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/user-1?transfer_to=lee", expect.objectContaining({ method: "DELETE" })));
  await waitFor(() => expect(screen.queryByText("alice")).not.toBeInTheDocument());
});
