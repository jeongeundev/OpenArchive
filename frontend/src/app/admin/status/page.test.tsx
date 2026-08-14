import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import AdminStatusPage from "./page";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const status = {
  node_address: "192.168.64.4",
  node_port: 6432,
  jobs: { pending: 2, processing: 0, recovery_pending: 0, error: 0 },
  zombie_timeout_minutes: 5,
  last_job_finished_at: null,
  inconsistent_documents: 0,
  embedding_provider: "fake",
};

describe("시스템 상태 화면", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("인증 확인 중에는 로딩 문구만 표시한다", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    render(<AuthProvider><AdminStatusPage /></AuthProvider>);

    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    expect(screen.queryByText("로그인이 필요합니다.")).not.toBeInTheDocument();
  });

  it("미로그인 상태에서는 안내하고 시스템 상태를 조회하지 않는다", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(response({
        authenticated: false,
        username: null,
        is_admin: false,
      }));
      if (url === "/api/system/status") return Promise.resolve(response(status));
      return Promise.resolve(response([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AdminStatusPage /></AuthProvider>);

    expect(await screen.findByText("로그인이 필요합니다.")).toBeInTheDocument();
    const requestedUrls = fetchMock.mock.calls.map(([url]) => url);
    expect(requestedUrls.filter((url) => url === "/api/system/status")).toHaveLength(0);
  });

  it("로그인 상태에서는 시스템 상태를 조회해 표시한다", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(response({
        authenticated: true,
        username: "alice",
        is_admin: false,
      }));
      if (url === "/api/system/status") return Promise.resolve(response(status));
      return Promise.resolve(response([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AdminStatusPage /></AuthProvider>);

    expect(await screen.findByText("fake")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/system/status",
      expect.objectContaining({ credentials: "same-origin" }),
    ));
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
