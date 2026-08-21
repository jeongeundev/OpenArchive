import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { wasPasswordChanged } from "@/lib/passwordChangeNotice";
import SettingsPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

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

  // /settings는 보호 경로다. 비밀번호를 바꾸면 세션이 끊겨 RequireAuth가 곧바로
  // /login으로 밀어내므로, 성공 안내는 이 화면에 둘 수 없고 로그인 화면으로 넘긴다.
  it("비밀번호를 바꾸면 안내를 넘기고 로그인 화면으로 보낸다", async () => {
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

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(wasPasswordChanged()).toBe(true);
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

  // 속성이 없어도 사람이 쓰는 데는 지장이 없다(HTML 기본값이 text). 다만 `type`으로
  // 거는 선택자에 걸리지 않아 이 칸에서 브라우저 자동화가 멈춘다 — /login과 같은 규칙이다.
  it("토큰 이름 칸도 type을 명시한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(response(alice)).mockResolvedValueOnce(response([])),
    );

    render(
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>,
    );

    expect(await screen.findByRole("textbox", { name: "토큰 이름" })).toHaveAttribute(
      "type",
      "text",
    );
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
