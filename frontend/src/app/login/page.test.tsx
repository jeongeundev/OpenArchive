import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { markPasswordChanged } from "@/lib/passwordChangeNotice";
import LoginPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("로그인 화면", () => {
  it("사용자명과 비밀번호 입력 및 제출 버튼을 렌더한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><LoginPage /></AuthProvider>);

    expect(await screen.findByRole("textbox", { name: "사용자명" })).toBeInTheDocument();
    expect(screen.getByLabelText("비밀번호")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  });

  // 속성이 없어도 사람이 쓰는 데는 지장이 없다(HTML 기본값이 text). 다만 `type`으로
  // 거는 선택자에 걸리지 않아 로그인을 거치는 브라우저 자동화가 이 칸에서 멈춘다.
  it("입력 두 칸 모두 type을 명시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><LoginPage /></AuthProvider>);

    expect(await screen.findByRole("textbox", { name: "사용자명" })).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("비밀번호")).toHaveAttribute("type", "password");
  });

  it("로그인 성공 후 문서 화면으로 이동한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ authenticated: false, username: null, is_admin: false }))
      .mockResolvedValueOnce(response({ authenticated: true, username: "alice", is_admin: false }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "사용자명" }), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));

    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it.each([401, 500])("실패 사유와 무관하게 같은 메시지를 표시한다 (%s)", async (status) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ authenticated: false, username: null, is_admin: false }))
      .mockResolvedValueOnce(response({ detail: status === 401 ? "인증 실패" : "서버 오류" }, status));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "사용자명" }), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByText("사용자명 또는 비밀번호를 확인하세요.")).toBeInTheDocument();
  });

  it("비밀번호를 바꾸고 밀려온 사람에게 그 사실을 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));
    markPasswordChanged();

    render(<AuthProvider><LoginPage /></AuthProvider>);

    expect(await screen.findByText(/비밀번호를 바꿨습니다/)).toBeInTheDocument();
  });

  it("그 안내는 한 번만 뜬다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));
    markPasswordChanged();

    const first = render(<AuthProvider><LoginPage /></AuthProvider>);
    await screen.findByText(/비밀번호를 바꿨습니다/);
    first.unmount();
    render(<AuthProvider><LoginPage /></AuthProvider>);

    await screen.findByRole("button", { name: "로그인" });
    expect(screen.queryByText(/비밀번호를 바꿨습니다/)).not.toBeInTheDocument();
  });
});
