import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentAccess, DocumentDetail } from "@/lib/types";
import { AuthProvider } from "@/components/AuthProvider";
import { DocumentDetailView } from "./DocumentDetailView";

// useRouter는 DocumentActions가 삭제 후 목록으로 보낼 때만 쓴다. usePathname은 문서 ID의
// 출처다 — 정적 export에서는 params에 껍데기 값이 들어오므로 URL에서 읽는다.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/documents/document-1",
}));

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
  extraction_status: "done",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  files: [],
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
    if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
    if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: ["alice", "bob"], groups: [] }));
    if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
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
  // 마운트 직후 useEffect가 문서·링크를 부르므로 render를 await된 act 안에서 돌린다.
  await act(async () => {
    render(
      <AuthProvider>
        <DocumentDetailView />
      </AuthProvider>,
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
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: [], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
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

  // 조회가 실패했을 때 links를 빈 배열로 두면 본문의 모든 링크가 깨진 링크로 그려진다.
  // ADR-027이 깨짐과 비공개를 구분되지 않게 만든 탓에 사용자는 오해를 되돌릴 단서가 없다.
  it("링크를 불러오지 못하면 깨진 링크로 그리지 않고 그 사실을 말한다", async () => {
    const linkedDetail = { ...detail, content: "[[대상 문서]]를 참고합니다." };
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links") || url.endsWith("/backlinks")) {
        return Promise.resolve(jsonResponse({ detail: "링크를 불러오지 못했습니다." }, 500));
      }
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: [], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      return Promise.resolve(jsonResponse(linkedDetail));
    }));

    await renderPage();

    expect(await screen.findByText("문서 링크를 불러오지 못했습니다.")).toBeInTheDocument();
    // 깨진 링크의 모양(점선)으로 그리지 않는다 — 본문은 원문 그대로 남는다.
    const brokenLike = screen.queryByText("대상 문서");
    expect(brokenLike).not.toBeInTheDocument();
    expect(screen.getByText(/\[\[대상 문서\]\]를 참고합니다\./)).toBeInTheDocument();
  });

  it("백링크가 없으면 영역 자체를 표시하지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: [], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      return Promise.resolve(jsonResponse(detail));
    }));

    await renderPage();

    expect(screen.queryByRole("heading", { name: "이 문서를 가리키는 문서" })).not.toBeInTheDocument();
  });
});

// 응답하지 않는 서버 — 화면을 떠날 때 요청이 취소되는지만 본다.
function pendingFetch() {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}));
}

describe("문서 상세 화면 취소", () => {
  it("화면을 떠나면 문서·관련 문서·링크 조회를 모두 취소한다", () => {
    const fetchMock = pendingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<AuthProvider><DocumentDetailView /></AuthProvider>);
    unmount();

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls).toEqual(
      expect.arrayContaining([
        "/api/documents/document-1",
        "/api/documents/document-1/links",
        "/api/documents/document-1/backlinks",
      ]),
    );
    for (const [, init] of fetchMock.mock.calls) expect(init?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("텍스트 인식에 실패한 문서", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("관련 문서·태그 추천이 오지 않을 임베딩 완료를 약속하지 않는다", async () => {
    const failed: DocumentDetail = {
      ...detail,
      filename: "blank.png",
      content_type: "png",
      content: "",
      version: 1,
      embedding_status: "pending",
      extraction_status: "failed",
      chunk_count: 0,
      chunk_version: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/auth/me") {
          return Promise.resolve(
            jsonResponse({ authenticated: true, username: "alice", is_admin: false }),
          );
        }
        if (url.endsWith("/links") || url.endsWith("/backlinks")) {
          return Promise.resolve(jsonResponse([]));
        }
        if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
        if (url === "/api/principals") {
          return Promise.resolve(jsonResponse({ users: [], groups: [] }));
        }
        if (url.endsWith("/access")) {
          return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
        }
        if (url.endsWith("/related")) {
          return Promise.resolve(
            jsonResponse({ items: [], identical: [], based_on_version: null, reason: "not_indexed" }),
          );
        }
        if (url.endsWith("/tag-suggestions")) {
          return Promise.resolve(
            jsonResponse({ items: [], based_on_version: null, reason: "not_indexed" }),
          );
        }
        return Promise.resolve(jsonResponse(failed));
      }),
    );
    await renderPage();

    expect(
      await screen.findAllByText("텍스트를 인식하지 못해 표시할 수 없습니다."),
    ).toHaveLength(2);
    expect(screen.queryByText("임베딩이 완료되면 표시됩니다.")).not.toBeInTheDocument();
  });
});

describe("문서 상세의 열람 범위 패널", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubAccessFetch(username: string) {
    let current: DocumentDetail = detail;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/auth/me") {
        return Promise.resolve(jsonResponse({ authenticated: true, username, is_admin: false }));
      }
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") {
        return Promise.resolve(jsonResponse({ users: ["alice", "bob"], groups: ["인사팀"] }));
      }
      if (url.endsWith("/access") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as DocumentAccess;
        current = { ...current, visibility: body.visibility };
        return Promise.resolve(jsonResponse(body));
      }
      if (url.endsWith("/access")) {
        return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      }
      return Promise.resolve(jsonResponse(current));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("소유자에게 패널을 보이고, 저장하면 문서 메타의 열람 범위가 갱신된다", async () => {
    stubAccessFetch("alice");
    await renderPage();

    expect(await screen.findByRole("heading", { name: "열람 범위" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(screen.getByText("열람 범위", { selector: "dt" }).nextElementSibling).toHaveTextContent(
        "제한",
      ),
    );
  });

  it("소유자가 아니면 패널을 보이지 않고 열람 범위를 조회하지도 않는다", async () => {
    const fetchMock = stubAccessFetch("bob");
    await renderPage();

    expect(screen.queryByRole("heading", { name: "열람 범위" })).not.toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url.endsWith("/access"))).toBe(false);
    expect(urls).not.toContain("/api/principals");
    expect(urls).not.toContain("/api/shares");
  });
});
