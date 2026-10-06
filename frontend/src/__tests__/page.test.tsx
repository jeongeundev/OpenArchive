import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import Home from "@/app/page";
import type { DocumentSummary } from "@/lib/types";

const documents: DocumentSummary[] = [
  {
    id: "document-1",
    title: "OpenSQL 운영 가이드",
    filename: "guide.md",
    content_type: "md",
    version: 1,
    owner_id: "alice",
    visibility: "public",
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

function mockFinder(total: number, globalTotal: number) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const body = url.pathname === "/api/auth/me" ? { authenticated: true, username: "alice", is_admin: false }
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
});
