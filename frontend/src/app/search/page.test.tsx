import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import SearchPage from "./page";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const searchResponse = { items: [], sql: "SELECT 1" };
const askResponse = {
  status: "answered",
  answer: "OpenProxy가 연결한다 [1]",
  detail: null,
  sources: [{
    label: 1, document_id: "document-1", title: "운영 규정", chunk_index: 0,
    based_on_version: 1, current_version: 1, revised: false, content: "근거", cited: true,
  }],
  items: [],
};

function stubFetch(authenticated: boolean) {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
    if (url === "/api/auth/me") {
      return Promise.resolve(jsonResponse({ authenticated, username: authenticated ? "alice" : null, is_admin: false }));
    }
    if (url === "/api/ask") return Promise.resolve(jsonResponse(askResponse));
    return Promise.resolve(jsonResponse(searchResponse));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderPage(): Promise<void> {
  await act(async () => {
    render(
      <AuthProvider>
        <SearchPage />
      </AuthProvider>,
    );
  });
}

async function searchFor(query: string): Promise<void> {
  fireEvent.change(screen.getByLabelText("검색어"), { target: { value: query } });
  await act(async () => {
    fireEvent.submit(screen.getByLabelText("검색어").closest("form") as HTMLFormElement);
  });
}

describe("검색 화면의 근거 기반 답변", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("방금 검색한 입력 그대로 답변을 묻는다", async () => {
    const fetchMock = stubFetch(true);
    await renderPage();
    await searchFor("장애 복구");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "이 검색어로 답변 받기" }));
    });

    const askCall = fetchMock.mock.calls.find(([url]) => url === "/api/ask");
    const searchCall = fetchMock.mock.calls.find(([url]) => url === "/api/search");
    expect(askCall?.[1]?.body).toBe(searchCall?.[1]?.body);
    expect(await screen.findByTestId("answer-text")).toHaveTextContent("OpenProxy가 연결한다 [1]");
  });

  it("새로 검색하면 이전 검색어의 답을 지운다", async () => {
    stubFetch(true);
    await renderPage();
    await searchFor("장애 복구");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "이 검색어로 답변 받기" }));
    });
    await screen.findByTestId("answer-text");

    await searchFor("보존 기간");

    await waitFor(() => expect(screen.queryByTestId("answer-text")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "이 검색어로 답변 받기" })).toBeInTheDocument();
  });

  it("검색 전과 로그인하지 않은 사용자에게는 답변 패널을 보이지 않는다", async () => {
    stubFetch(false);
    await renderPage();

    expect(screen.queryByRole("heading", { name: "근거 기반 답변" })).not.toBeInTheDocument();
    await searchFor("장애 복구");

    expect(screen.queryByRole("heading", { name: "근거 기반 답변" })).not.toBeInTheDocument();
  });
});
