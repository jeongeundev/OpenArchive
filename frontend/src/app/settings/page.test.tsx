import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { wasPasswordChanged } from "@/lib/passwordChangeNotice";
import SettingsPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
// 외부 공유 절은 자기 테스트(SharesSection.test.tsx)가 있다. 여기서는 토큰·비밀번호 절의
// fetch 순서를 지키도록 빼 둔다.
vi.mock("@/components/SharesSection", () => ({ SharesSection: () => null }));

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
    created_at: "2026-08-21T00:00:00Z", expires_at: null, last_used_at: null, expired: false,
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
            created_at: "2026-08-21T01:00:00Z", expires_at: null, last_used_at: null, expired: false,
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
            created_at: "2026-08-21T01:00:00Z", expires_at: null, last_used_at: null, expired: false,
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
});

describe("계정 설정 화면 취소", () => {
  it("화면을 떠나면 진행 중인 토큰 목록 조회를 취소한다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(alice));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><SettingsPage /></AuthProvider>);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    unmount();

    expect(fetchMock.mock.calls[1][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("계정 설정 화면 — 동작 뒤 조회 취소", () => {
  it("발급 뒤 목록을 다시 받는 중에 화면을 떠나면 그 조회를 취소한다 — 발급 요청은 건드리지 않는다", async () => {
    const fetchMock = vi
      .fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}))
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response(tokens))
      .mockResolvedValueOnce(
        response(
          {
            id: "token-2",
            name: "배치 투입",
            scope: "read",
            created_at: "2026-08-21T01:00:00Z", expires_at: null, last_used_at: null, expired: false,
            token: "plaintext-shown-once",
          },
          201,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><SettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "배치 투입" },
    });
    fireEvent.click(screen.getByRole("button", { name: "토큰 발급" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    unmount();

    expect(fetchMock.mock.calls[2][1]?.signal).toBeFalsy();
    expect(fetchMock.mock.calls[3][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("계정 설정 화면 — 토큰 만료·마지막 사용", () => {
  const listed = [
    {
      id: "token-1",
      name: "옛 연동",
      scope: "read",
      created_at: "2026-08-21T00:00:00Z",
      expires_at: "2026-09-01T00:00:00Z",
      last_used_at: "2026-08-30T03:00:00Z",
      expired: true,
    },
    {
      id: "token-2",
      name: "상시 연동",
      scope: "read_write",
      created_at: "2026-08-22T00:00:00Z",
      expires_at: null,
      last_used_at: null,
      expired: false,
    },
  ];

  function row(name: string): HTMLElement {
    const cell = screen.getByText(name);
    const tr = cell.closest("tr");
    if (tr === null) throw new Error(`${name} 행이 없다`);
    return tr;
  }

  it("만료일을 고르면 그날 끝까지 유효한 시각으로 발급을 요청한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(
        response(
          {
            id: "token-3",
            name: "기한부",
            scope: "read",
            created_at: "2026-10-08T00:00:00Z",
            expires_at: new Date(2026, 10, 1).toISOString(),
            last_used_at: null,
            expired: false,
            token: "plaintext-once",
          },
          201,
        ),
      )
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><SettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "기한부" },
    });
    const date = screen.getByLabelText("만료일 (선택)");
    expect(date).toHaveAttribute("type", "date");
    fireEvent.change(date, { target: { value: "2026-10-31" } });
    fireEvent.click(screen.getByRole("button", { name: "토큰 발급" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      name: "기한부",
      scope: "read",
      expires_at: new Date(2026, 10, 1).toISOString(),
    });
    await waitFor(() => expect(screen.getByLabelText("만료일 (선택)")).toHaveValue(""));
  });

  it("만료일을 비우면 만료 없이 발급한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(alice))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(
        response(
          {
            id: "token-3",
            name: "무기한",
            scope: "read",
            created_at: "2026-10-08T00:00:00Z",
            expires_at: null,
            last_used_at: null,
            expired: false,
            token: "plaintext-once",
          },
          201,
        ),
      )
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><SettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "무기한" },
    });
    fireEvent.click(screen.getByRole("button", { name: "토큰 발급" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      name: "무기한",
      scope: "read",
    });
  });

  it("표에 「만료」·「마지막 사용」 열이 있고, 서버가 만료로 판정한 행에만 「만료」를 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(response(alice)).mockResolvedValueOnce(response(listed)),
    );

    render(<AuthProvider><SettingsPage /></AuthProvider>);
    await screen.findByText("옛 연동");

    expect(screen.getByRole("columnheader", { name: "만료" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "마지막 사용" })).toBeInTheDocument();

    const expiredRow = row("옛 연동");
    expect(within(expiredRow).getByText("만료")).toBeInTheDocument();
    expect(within(expiredRow).queryByText("사용 기록 없음")).not.toBeInTheDocument();

    const liveRow = row("상시 연동");
    expect(within(liveRow).queryByText("만료")).not.toBeInTheDocument();
    expect(within(liveRow).getByText("없음")).toBeInTheDocument();
    expect(within(liveRow).getByText("사용 기록 없음")).toBeInTheDocument();
    // 목록에는 원문 토큰이 없다 (TC 270)
    expect(screen.queryByText(/plaintext/)).not.toBeInTheDocument();
  });

  it("과거 만료일 거부의 detail을 오류 자리에 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response(alice))
        .mockResolvedValueOnce(response([]))
        .mockResolvedValueOnce(response({ detail: "만료일은 지금 이후여야 합니다." }, 400)),
    );

    render(<AuthProvider><SettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "기한부" },
    });
    // 오늘보다 이른 날짜는 입력의 min이 제출 전에 막는다. 서버 판정(DB now)과 브라우저
    // 날짜가 어긋난 경우의 400을 흉내 내려고 통과하는 날짜를 넣는다.
    fireEvent.change(screen.getByLabelText("만료일 (선택)"), { target: { value: "2099-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "토큰 발급" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("만료일은 지금 이후여야 합니다.");
  });
});
