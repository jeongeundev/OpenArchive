import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import SettingsPage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const alice = { authenticated: true, username: "alice", is_admin: false };
const tokens = [
  {
    id: "token-1",
    name: "노트북 CLI",
    scope: "read",
    created_at: "2026-08-21T00:00:00Z",
  },
];

describe("계정 설정 화면", () => {
  it("발급한 토큰의 원문을 한 번만 보여주고 다시 볼 수 없다고 알린다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response(tokens))
      .mockResolvedValueOnce(
        response(
          {
            id: "token-2",
            name: "배치 투입",
            scope: "read_write",
            created_at: "2026-08-21T01:00:00Z",
            token: "plaintext-shown-once",
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        response([
          ...tokens,
          {
            id: "token-2",
            name: "배치 투입",
            scope: "read_write",
            created_at: "2026-08-21T01:00:00Z",
          },
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );
    fireEvent.change(await screen.findByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "배치 투입" },
    });
    fireEvent.change(screen.getByLabelText("권한 범위"), {
      target: { value: "read_write" },
    });
    fireEvent.click(screen.getByRole("button", { name: "토큰 발급" }));

    expect(await screen.findByText("plaintext-shown-once")).toBeInTheDocument();
    expect(screen.getByText(/다시 볼 수 없습니다/)).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      name: "배치 투입",
      scope: "read_write",
    });
  });

  it("목록에는 원문 없이 이름·범위만 남는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(response(alice)).mockResolvedValueOnce(response(tokens)),
    );

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );

    expect(await screen.findByText("노트북 CLI")).toBeInTheDocument();
    expect(screen.getByText("읽기 전용")).toBeInTheDocument();
    expect(screen.queryByText(/plaintext/)).not.toBeInTheDocument();
  });

  it("확인 후 토큰을 폐기하고 목록을 갱신한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response(tokens))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "폐기" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/auth/tokens/token-1",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
    await waitFor(() => expect(screen.queryByText("노트북 CLI")).not.toBeInTheDocument());
  });

  it("비밀번호를 바꾸면 다시 로그인해야 한다고 알린다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response(tokens))
      .mockResolvedValueOnce(
        response({ authenticated: false, username: null, is_admin: false }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );
    fireEvent.change(await screen.findByLabelText("현재 비밀번호"), {
      target: { value: "old-secret" },
    });
    fireEvent.change(screen.getByLabelText("새 비밀번호"), {
      target: { value: "new-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));

    expect(
      await screen.findByText(/모든 기기의 로그인이 끊겼습니다. 새 비밀번호로 다시 로그인하세요/),
    ).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      current_password: "old-secret",
      new_password: "new-secret",
    });
  });

  it("현재 비밀번호가 틀리면 백엔드가 준 이유를 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response(alice))
        .mockResolvedValueOnce(response(tokens))
        .mockResolvedValueOnce(
          response({ detail: "현재 비밀번호가 올바르지 않습니다." }, 403),
        ),
    );

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );
    fireEvent.change(await screen.findByLabelText("현재 비밀번호"), {
      target: { value: "wrong" },
    });
    fireEvent.change(screen.getByLabelText("새 비밀번호"), {
      target: { value: "new-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));

    expect(
      await screen.findByText("현재 비밀번호가 올바르지 않습니다."),
    ).toBeInTheDocument();
  });

  it("로그인하지 않았으면 아무 자격증명도 보이지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({ authenticated: false, username: null, is_admin: false }),
      ),
    );

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );

    expect(await screen.findByText("로그인이 필요합니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "토큰 발급" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "비밀번호 변경" })).not.toBeInTheDocument();
  });
});
