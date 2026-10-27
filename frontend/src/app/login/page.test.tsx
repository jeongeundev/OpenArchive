// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { markPasswordChanged } from "@/lib/passwordChangeNotice";
import LoginPage from "./page";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getHealth: vi.fn().mockResolvedValue({ status: "ok" }),
}));

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("로그인 화면", () => {
  it("제품 소개와 계정 안내, 서버 상태를 보여준다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ authenticated: false, username: null, is_admin: false })));
    render(<AuthProvider><LoginPage /></AuthProvider>);
    for (const text of ["OpenArchive", "조직의 문서를,", "질문 하나로 찾습니다.", "의미 기반 AI 검색", "부서·폴더 단위 권한", "문서 관계 지도", "관리자에게 발급받은 계정으로 로그인하세요.", "비밀번호를 잊었으면 관리자에게 재설정을 요청하세요."]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(await screen.findByText("서버 연결됨")).toBeInTheDocument();
    expect(screen.getByText(window.location.host)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /비밀번호/ })).not.toBeInTheDocument();
  });
  it("사용자명과 비밀번호 입력 및 제출 버튼을 렌더한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><LoginPage /></AuthProvider>);

    expect(await screen.findByRole("textbox", { name: "아이디" })).toBeInTheDocument();
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

    expect(await screen.findByRole("textbox", { name: "아이디" })).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("비밀번호")).toHaveAttribute("type", "password");
  });

  it("로그인 카드를 화면 가운데에 놓는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      authenticated: false,
      username: null,
      is_admin: false,
    })));

    render(<AuthProvider><LoginPage /></AuthProvider>);

    const button = await screen.findByRole("button", { name: "로그인" });
    expect(button.closest("section")).toHaveClass("mx-auto", "max-w-sm");
  });

  it("로그인 성공 후 문서 화면으로 이동한다", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ authenticated: false, username: null, is_admin: false }))
      .mockResolvedValueOnce(response({ authenticated: true, username: "alice", is_admin: false }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "아이디" }), { target: { value: "alice" } });
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
    fireEvent.change(await screen.findByRole("textbox", { name: "아이디" }), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByText("사용자명 또는 비밀번호를 확인하세요.")).toBeInTheDocument();
  });

  it("시도가 너무 많아 잠기면(429) 서버가 준 안내를 보여준다", async () => {
    const detail = "로그인 시도가 너무 많습니다. 잠시 뒤 다시 시도하세요.";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ authenticated: false, username: null, is_admin: false }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail }), {
        status: 429,
        headers: { "Content-Type": "application/json", "Retry-After": "60" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "아이디" }), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "anything" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.queryByText("사용자명 또는 비밀번호를 확인하세요.")).not.toBeInTheDocument();
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
