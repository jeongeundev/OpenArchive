import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetail } from "@/lib/types";
import { AuthProvider } from "@/components/AuthProvider";
import DocumentDetailPage from "./page";

// DocumentActions가 삭제 후 목록으로 보낼 때만 쓴다. 태그 편집 경로와는 무관하다.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const detail: DocumentDetail = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  content: "추출된 텍스트",
  version: 2,
  owner_id: "alice",
  visibility: "public",
  tags: ["OpenSQL"],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  chunk_count: 1,
  chunk_version: 2,
};

const related = { items: [], identical: [], based_on_version: 2, reason: null };
const suggestions = {
  items: [{ tag: "pgvector", freq: 2 }],
  based_on_version: 2,
  reason: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(tagsResponse: () => Response) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
    if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
    if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
    if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
    if (url.endsWith("/tags") && init?.method === "PUT") {
      return Promise.resolve(tagsResponse());
    }
    return Promise.resolve(jsonResponse(detail));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** PUT /tags 로 실제로 넘어간 태그 목록. */
function savedTags(fetchMock: ReturnType<typeof stubFetch>): string[][] {
  return fetchMock.mock.calls
    .filter(([url, init]) => url.endsWith("/tags") && init?.method === "PUT")
    .map(([, init]) => (JSON.parse(String(init?.body)) as { tags: string[] }).tags);
}

async function renderPage(): Promise<void> {
  // params는 렌더마다 새로 만들면 use()가 계속 서스펜드한다. 한 번만 만든다.
  const params = Promise.resolve({ id: "document-1" });
  // use(params)가 서스펜드하므로 render를 await된 act 안에서 돌려야 재개된다.
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <AuthProvider><DocumentDetailPage params={params} /></AuthProvider>
      </Suspense>,
    );
  });
  await screen.findByRole("button", { name: "태그 저장" });
}

describe("문서 상세 페이지의 태그 편집", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("추천 태그를 클릭하면 현재 태그에 더한 목록으로 저장한다", async () => {
    const fetchMock = stubFetch(() => jsonResponse(detail));
    await renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "pgvector 태그 적용" }));

    await waitFor(() => expect(savedTags(fetchMock)).toEqual([["OpenSQL", "pgvector"]]));
  });

  it("저장하지 않은 편집 중 태그가 추천 적용으로 사라지지 않는다", async () => {
    const fetchMock = stubFetch(() => jsonResponse(detail));
    await renderPage();

    fireEvent.change(screen.getByRole("textbox", { name: "태그" }), {
      target: { value: "임시" },
    });
    fireEvent.click(screen.getByRole("button", { name: "태그 추가" }));
    fireEvent.click(await screen.findByRole("button", { name: "pgvector 태그 적용" }));

    await waitFor(() =>
      expect(savedTags(fetchMock)).toEqual([["OpenSQL", "임시", "pgvector"]]),
    );
    expect(screen.getByRole("button", { name: "임시 태그 삭제" })).toBeInTheDocument();
  });

  it("저장에 실패하면 문구를 남기고 편집한 태그를 유지한다", async () => {
    stubFetch(() =>
      jsonResponse({ detail: "태그를 수정할 권한이 없습니다." }, 403),
    );
    await renderPage();

    fireEvent.change(screen.getByRole("textbox", { name: "태그" }), {
      target: { value: "임시" },
    });
    fireEvent.click(screen.getByRole("button", { name: "태그 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "태그 저장" }));

    expect(
      await screen.findByText("태그를 수정할 권한이 없습니다."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "임시 태그 삭제" })).toBeInTheDocument();
  });
});

describe("문서 상세 페이지의 위키링크", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("API가 해석한 본문 링크와 백링크를 표시한다", async () => {
    const linkedDetail = { ...detail, content: "[[대상 문서]]를 참고합니다." };
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links")) return Promise.resolve(jsonResponse([{ title: "대상 문서", document_id: "target-1" }]));
      if (url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([{ document_id: "source-1", title: "출발 문서" }]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      return Promise.resolve(jsonResponse(linkedDetail));
    }));

    await renderPage();

    expect(await screen.findByRole("link", { name: "대상 문서" })).toHaveAttribute(
      "href",
      "/documents/target-1",
    );
    expect(screen.getByRole("heading", { name: "이 문서를 가리키는 문서" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "출발 문서" })).toHaveAttribute(
      "href",
      "/documents/source-1",
    );
  });

  it("백링크가 없으면 영역 자체를 표시하지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      return Promise.resolve(jsonResponse(detail));
    }));

    await renderPage();

    expect(screen.queryByRole("heading", { name: "이 문서를 가리키는 문서" })).not.toBeInTheDocument();
  });
});
