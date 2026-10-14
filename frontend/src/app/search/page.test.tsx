// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import { RequireAuth } from "@/components/RequireAuth";
import SearchPage from "./page";

// 주소를 실제로 바꾸는 라우터 — 검색 화면은 제출한 조건을 주소에 두고 주소를 기준으로 검색한다.
const navigation = vi.hoisted(() => {
  let params = new URLSearchParams();
  const listeners = new Set<() => void>();
  const go = (href: string) => {
    params = new URL(href, "http://localhost").searchParams;
    listeners.forEach((listener) => listener());
  };
  return {
    params: () => params,
    go,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    push: vi.fn(go),
  };
});
vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useRouter: () => ({ push: navigation.push, replace: navigation.go }),
    usePathname: () => "/search",
    useSearchParams: () => useSyncExternalStore(navigation.subscribe, navigation.params),
  };
});

afterEach(() => {
  navigation.go("/search");
  navigation.push.mockClear();
});

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

/** 실제 앱처럼 로그인 확인 뒤에 그린다(/search는 공개 경로가 아니다) — 주소의 폴더를 폴더 목록이 오기 전에 판정하지 않는다. */
async function renderSignedInPage(): Promise<void> {
  await act(async () => {
    render(
      <AuthProvider>
        <RequireAuth>
          <SearchPage />
        </RequireAuth>
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

function searchCalls(fetchMock: ReturnType<typeof stubFetch>): Record<string, unknown>[] {
  return fetchMock.mock.calls.filter(([url]) => url === "/api/search").map(([, init]) => JSON.parse(String(init?.body)));
}

describe("검색 조건 주소", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("검색하면 조건을 주소에 남긴다", async () => {
    stubFetch(true);
    await renderSignedInPage();
    fireEvent.change(screen.getByLabelText("태그 (쉼표로 구분)"), { target: { value: "인사, 복무" } });
    await searchFor("연차 휴가");

    expect(navigation.push).toHaveBeenCalledExactlyOnceWith(expect.stringMatching(/^\/search\?/));
    const params = new URL(String(navigation.push.mock.calls[0][0]), "http://localhost").searchParams;
    expect(params.get("q")).toBe("연차 휴가");
    expect(params.get("tags")).toBe("인사,복무");
  });

  it("주소의 조건으로 폼을 채우고 검색하지만, 답변은 다시 만들지 않는다", async () => {
    navigation.go("/search?q=장애 복구&tags=운영&type=pdf&k=20");
    const fetchMock = stubFetch(true);
    await renderSignedInPage();

    await waitFor(() => expect(searchCalls(fetchMock)).toHaveLength(1));
    expect(searchCalls(fetchMock)[0]).toMatchObject({ query: "장애 복구", tags: ["운영"], content_type: "pdf", k: 20 });
    expect(screen.getByLabelText("검색어")).toHaveValue("장애 복구");
    expect(screen.getByLabelText("태그 (쉼표로 구분)")).toHaveValue("운영");
    expect(screen.getByLabelText("문서 유형")).toHaveValue("pdf");
    expect(screen.getByLabelText("결과 수")).toHaveValue(20);
    expect(screen.getByRole("button", { name: "이 검색어로 답변 받기" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/ask")).toBe(false);
  });

  it("같은 조건으로 다시 검색해도 다시 조회한다", async () => {
    const fetchMock = stubFetch(true);
    await renderSignedInPage();
    await searchFor("장애 복구");
    await waitFor(() => expect(searchCalls(fetchMock)).toHaveLength(1));

    await searchFor("장애 복구");

    await waitFor(() => expect(searchCalls(fetchMock)).toHaveLength(2));
  });

  it("주소의 폴더를 볼 수 있으면 그 폴더로 검색하고 폼에도 고른다", async () => {
    navigation.go("/search?q=채용&folder=f-hr");
    const fetchMock = stubFetch(true);
    await renderSignedInPage();

    await waitFor(() => expect(searchCalls(fetchMock)).toHaveLength(1));
    expect(searchCalls(fetchMock)[0]).toMatchObject({ folder_id: "f-hr" });
    expect(screen.getByLabelText("폴더")).toHaveValue("f-hr");
  });

  it("주소의 폴더를 볼 수 없으면 폴더 없이 검색한다", async () => {
    navigation.go("/search?q=채용&folder=hidden");
    const fetchMock = stubFetch(true);
    await renderSignedInPage();

    await waitFor(() => expect(searchCalls(fetchMock)).toHaveLength(1));
    expect(searchCalls(fetchMock)[0]).not.toHaveProperty("folder_id");
  });

  it("조건 없는 주소로 오면 폼과 결과를 비운다", async () => {
    stubFetch(true);
    await renderSignedInPage();
    await searchFor("장애 복구");
    expect(screen.getByRole("button", { name: "이 검색어로 답변 받기" })).toBeInTheDocument();

    await act(async () => navigation.go("/search"));

    expect(screen.getByLabelText("검색어")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "이 검색어로 답변 받기" })).not.toBeInTheDocument();
  });
});
