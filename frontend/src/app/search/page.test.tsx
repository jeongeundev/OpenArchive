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

const hrFolder = {
  id: "f-hr", parent_id: null, name: "인사", created_by: "lee", document_count: 1,
  scope: { visibility: "public", users: [], groups: [] },
  inherited: false, can_manage: false, can_change_access: false, can_share: false,
};

function stubFetch(authenticated: boolean, foldersStatus = 200) {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
    if (url === "/api/folders") {
      return Promise.resolve(foldersStatus === 200
        ? jsonResponse([hrFolder])
        : new Response(JSON.stringify({ detail: "공유 토큰으로는 쓸 수 없습니다." }), {
          status: foldersStatus, headers: { "Content-Type": "application/json" },
        }));
    }
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

describe("검색 화면의 폴더 필터", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("고른 폴더를 검색과 답변 요청에 같은 folder_id로 보낸다", async () => {
    const fetchMock = stubFetch(true);
    await renderPage();
    const select = await screen.findByLabelText("폴더");
    fireEvent.change(select, { target: { value: "f-hr" } });
    await searchFor("채용 절차");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "이 검색어로 답변 받기" }));
    });

    const searchCall = fetchMock.mock.calls.find(([url]) => url === "/api/search");
    const askCall = fetchMock.mock.calls.find(([url]) => url === "/api/ask");
    expect(JSON.parse(String(searchCall?.[1]?.body))).toMatchObject({ query: "채용 절차", folder_id: "f-hr" });
    expect(JSON.parse(String(askCall?.[1]?.body))).toMatchObject({ folder_id: "f-hr" });
  });

  it("폴더 목록을 볼 수 없으면 폴더 칸 없이 검색한다", async () => {
    const fetchMock = stubFetch(true, 403);
    await renderPage();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/folders")).toBe(true));

    expect(screen.queryByLabelText("폴더")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await searchFor("채용 절차");

    const searchCall = fetchMock.mock.calls.find(([url]) => url === "/api/search");
    expect(JSON.parse(String(searchCall?.[1]?.body))).not.toHaveProperty("folder_id");
  });

  it("로그인하지 않으면 폴더 목록을 부르지 않는다", async () => {
    const fetchMock = stubFetch(false);
    await renderPage();
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/folders")).toBe(false);
    expect(screen.queryByLabelText("폴더")).not.toBeInTheDocument();
  });
});
