import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import Home from "@/app/page";
import type { DocumentSummary, Folder } from "@/lib/types";

const navigation = vi.hoisted(() => ({ searchParams: new URLSearchParams(), push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  useSearchParams: () => navigation.searchParams,
}));

const documents: DocumentSummary[] = [
  {
    id: "document-1",
    title: "OpenSQL 운영 가이드",
    filename: "guide.md",
    content_type: "md",
    version: 1,
    owner_id: "alice",
    visibility: "public",
    effective_visibility: "public",
    tags: ["OpenSQL"],
    embedding_status: "ready",
    extraction_status: "done",
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T11:00:00Z",
  },
  {
    id: "document-2",
    title: "정합성 점검표",
    filename: "checklist.txt",
    content_type: "txt",
    version: 1,
    owner_id: "alice",
    visibility: "private",
    effective_visibility: "private",
    tags: ["정합성"],
    embedding_status: "pending",
    extraction_status: "done",
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T11:00:00Z",
  },
];

describe("루트 페이지", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("조회한 문서 목록을 상세 링크로 표시한다", async () => {
    mockFinder(2, 2);

    render(<Home />);

    expect(screen.getByRole("heading", { name: "문서" })).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "OpenSQL 운영 가이드" }),
    ).toHaveAttribute("href", "/documents/document-1");
    expect(screen.getByRole("link", { name: "정합성 점검표" })).toHaveAttribute(
      "href",
      "/documents/document-2",
    );
    expect(screen.queryByRole("button", { name: "업로드" })).not.toBeInTheDocument();
  });

  it("처리 현황만 불러오지 못하면 전체 수를 모른다고 알린다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          String(input).startsWith("/api/documents/progress")
            ? new Response(JSON.stringify({ detail: "집계 실패" }), {
                status: 500,
                headers: { "Content-Type": "application/json" },
              })
            : new Response(JSON.stringify(String(input).startsWith("/api/documents/tags") ? [] : String(input).startsWith("/api/documents/count") ? { total: 2 } : documents), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              }),
        ),
      ),
    );

    render(<Home />);

    expect(await screen.findByRole("link", { name: "OpenSQL 운영 가이드" })).toBeInTheDocument();
    expect(
      await screen.findByText(/처리 현황을 불러오지 못했습니다/),
    ).toBeInTheDocument();
  });
});

function mockFinder(total: number, globalTotal: number, folders: Folder[] = []) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const body = url.pathname === "/api/auth/me" ? { authenticated: true, username: "alice", is_admin: false }
      : url.pathname === "/api/folders" ? folders
      : url.pathname.endsWith("/progress") ? { extracting: 0, extraction_failed: 0, pending: 0, processing: 0, ready: globalTotal, error: 0 }
      : url.pathname.endsWith("/count") ? { total }
      : url.pathname.endsWith("/tags") ? ["보안"] : total === 0 ? [] : documents;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("목록 찾기", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("조건 건수 120·전체 300이면 세 페이지까지만 표시한다", async () => {
    mockFinder(120, 300);
    render(<Home />);
    expect(await screen.findByText("120건 중 1–50")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByText("120건 중 101–120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });
  it("조건을 함께 전달하고 조건이 바뀌면 첫 페이지에서 조회한다", async () => {
    const fetchMock = mockFinder(120, 300);
    render(<Home />);
    await screen.findByText("120건 중 1–50");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "출장" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("q="), expect.anything()));
    expect(screen.getByText("120건 중 1–50")).toBeInTheDocument();
    for (const [label, value] of [["문서 유형", "hwp"], ["태그", "보안"], ["정렬", "title"]]) {
      fireEvent.click(screen.getByRole("button", { name: "다음" }));
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
      expect(screen.getByText("120건 중 1–50")).toBeInTheDocument();
    }
    await waitFor(() => {
      const requests = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://localhost"));
      expect(requests.some((url) => url.pathname === "/api/documents" && url.searchParams.get("q") === "출장" && url.searchParams.get("content_type") === "hwp" && url.searchParams.get("tag") === "보안" && url.searchParams.get("sort") === "title" && url.searchParams.get("offset") === "0")).toBe(true);
    });
  });
  it("전체 문서가 있어도 조건 결과가 없으면 첫 사용 안내 대신 조건 안내를 표시한다", async () => {
    mockFinder(0, 300);
    render(<AuthProvider><Home /></AuthProvider>);
    fireEvent.change(screen.getByLabelText("문서 유형"), { target: { value: "hwp" } });
    expect(await screen.findByText("조건에 맞는 문서가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/openarchive demo/)).not.toBeInTheDocument();
    expect(screen.queryByText("아직 문서가 없습니다.")).not.toBeInTheDocument();
  });
  it("전체 문서가 없으면 첫 사용 안내를 유지한다", async () => {
    mockFinder(0, 0);
    render(<AuthProvider><Home /></AuthProvider>);
    expect(await screen.findByText(/openarchive demo --user alice/)).toBeInTheDocument();
    expect(screen.queryByText("조건에 맞는 문서가 없습니다.")).not.toBeInTheDocument();
  });
  it("업로드하면 새 태그가 태그 선택지에 나타난다", async () => {
    let uploaded = false;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (init?.method === "POST" && url.pathname === "/api/documents") uploaded = true;
      const body = url.pathname === "/api/auth/me" ? { authenticated: true, username: "alice", is_admin: false }
        : url.pathname === "/api/folders" ? []
        : url.pathname.endsWith("/progress") ? { extracting: 0, extraction_failed: 0, pending: 0, processing: 0, ready: 2, error: 0 }
        : url.pathname.endsWith("/count") ? { total: 2 }
        : url.pathname.endsWith("/tags") ? (uploaded ? ["보안", "신규"] : ["보안"])
        : init?.method === "POST" ? documents[0] : documents;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    render(<AuthProvider><Home /></AuthProvider>);
    fireEvent.change(await screen.findByLabelText("업로드할 파일"), {
      target: { files: [new File(["본문"], "new.md", { type: "text/markdown" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByRole("option", { name: "신규" })).toBeInTheDocument();
  });
});

const publicScope = { visibility: "public" as const, users: [], groups: [] };
const hr: Folder = { id: "folder-a", name: "인사", parent_id: null, created_by: "alice", document_count: 1,
  scope: publicScope, inherited: false, can_manage: true, can_change_access: true };

function requestedUrls(fetchMock: ReturnType<typeof vi.fn>): URL[] {
  return fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://localhost"));
}

describe("폴더 트리", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    navigation.searchParams = new URLSearchParams();
    navigation.push.mockReset();
    navigation.replace.mockReset();
  });

  it("폴더를 고르면 그 폴더에 든 문서만 목록과 건수로 조회하고 문서 수를 트리에 표시한다", async () => {
    navigation.searchParams = new URLSearchParams("folder=folder-a");
    const fetchMock = mockFinder(1, 2, [hr]);
    render(<AuthProvider><Home /></AuthProvider>);
    expect(await screen.findByRole("heading", { name: "인사" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /인사/ })).toHaveTextContent("1");
    await waitFor(() => {
      const urls = requestedUrls(fetchMock);
      expect(urls.some(url => url.pathname === "/api/documents" && url.searchParams.get("folder_id") === "folder-a")).toBe(true);
      expect(urls.some(url => url.pathname === "/api/documents/count" && url.searchParams.get("folder_id") === "folder-a")).toBe(true);
    });
  });

  it("폴더를 누르면 주소에 폴더를 두고, 「전체 문서」를 누르면 해제한다", async () => {
    mockFinder(2, 2, [hr]);
    render(<AuthProvider><Home /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /인사/ }));
    expect(navigation.push).toHaveBeenLastCalledWith("/?folder=folder-a");
    fireEvent.click(screen.getByRole("button", { name: "전체 문서" }));
    expect(navigation.push).toHaveBeenLastCalledWith("/");
  });

  it("볼 수 없는 폴더를 주소로 고르면 존재를 알리지 않고 전체 문서로 되돌린다", async () => {
    navigation.searchParams = new URLSearchParams("folder=hidden");
    const fetchMock = mockFinder(2, 2, [hr]);
    render(<AuthProvider><Home /></AuthProvider>);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByText(/찾을 수 없/)).not.toBeInTheDocument();
    expect(requestedUrls(fetchMock).some(url => url.searchParams.get("folder_id") === "hidden")).toBe(false);
  });

  it("빈 폴더를 고르면 폴더가 비었다고 알린다", async () => {
    navigation.searchParams = new URLSearchParams("folder=folder-a");
    mockFinder(0, 2, [{ ...hr, document_count: 0 }]);
    render(<AuthProvider><Home /></AuthProvider>);
    expect(await screen.findByText("이 폴더에 문서가 없습니다.")).toBeInTheDocument();
  });

  it("「새 폴더」로 만든 폴더가 트리에 나타나고, 최상위 폴더는 「조직 공개」로 표시된다", async () => {
    let folders: Folder[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/folders" && init?.method === "POST") {
        const created = { ...hr, document_count: 0, name: JSON.parse(String(init.body)).name };
        folders = [created];
        return Promise.resolve(new Response(JSON.stringify(created), { status: 201, headers: { "Content-Type": "application/json" } }));
      }
      const body = url.pathname === "/api/auth/me" ? { authenticated: true, username: "alice", is_admin: false }
        : url.pathname === "/api/folders" ? folders
        : url.pathname.endsWith("/progress") ? { extracting: 0, extraction_failed: 0, pending: 0, processing: 0, ready: 2, error: 0 }
        : url.pathname.endsWith("/count") ? { total: 2 }
        : url.pathname.endsWith("/tags") ? [] : documents;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const { rerender } = render(<AuthProvider><Home /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "새 폴더" }));
    fireEvent.change(screen.getByLabelText("새 폴더 이름"), { target: { value: "인사" } });
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));
    expect(await screen.findByRole("button", { name: /인사/ })).toBeInTheDocument();
    navigation.searchParams = new URLSearchParams("folder=folder-a");
    rerender(<AuthProvider><Home /></AuthProvider>);
    const header = (await screen.findByRole("heading", { name: "인사" })).parentElement as HTMLElement;
    expect(header).toHaveTextContent("열람 범위 · 조직 공개");
  });

  it("빈 폴더를 삭제하면 트리에서 사라지고 전체 문서로 돌아간다", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    navigation.searchParams = new URLSearchParams("folder=folder-a");
    let folders: Folder[] = [{ ...hr, document_count: 0 }];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (init?.method === "DELETE") {
        folders = [];
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      const body = url.pathname === "/api/auth/me" ? { authenticated: true, username: "alice", is_admin: false }
        : url.pathname === "/api/folders" ? folders
        : url.pathname.endsWith("/progress") ? { extracting: 0, extraction_failed: 0, pending: 0, processing: 0, ready: 2, error: 0 }
        : url.pathname.endsWith("/count") ? { total: 0 }
        : url.pathname.endsWith("/tags") ? [] : [];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    render(<AuthProvider><Home /></AuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
    await waitFor(() => expect(navigation.push).toHaveBeenCalledWith("/"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /인사/ })).not.toBeInTheDocument());
    vi.restoreAllMocks();
  });

  it("로그인하지 않으면 폴더 트리를 그리지 않는다", async () => {
    mockFinder(2, 2, [hr]);
    render(<Home />);
    await screen.findByRole("link", { name: "OpenSQL 운영 가이드" });
    expect(screen.queryByRole("button", { name: "전체 문서" })).not.toBeInTheDocument();
  });
});
