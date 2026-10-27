// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentAccess, DocumentDetail } from "@/lib/types";
import { AuthProvider } from "@/components/AuthProvider";
import { DocumentDetailView } from "./DocumentDetailView";
import { rememberListHref } from "@/lib/urlState";

// useRouter는 DocumentActions가 삭제 후 목록으로 보낼 때만 쓴다. usePathname은 문서 ID의
// 출처다 — 정적 export에서는 params에 껍데기 값이 들어오므로 URL에서 읽는다.
const navigation = vi.hoisted(() => ({ searchParams: new URLSearchParams(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: navigation.replace }),
  usePathname: () => "/documents/document-1",
  useSearchParams: () => navigation.searchParams,
}));

const detail: DocumentDetail = {
  folder: null,
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  content: "추출된 텍스트",
  version: 2,
  owner_id: "alice",
  visibility: "public",
  effective_visibility: "public",
  follows_folder: false,
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
    if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
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
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
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
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
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
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
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
        if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
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
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") {
        return Promise.resolve(jsonResponse({ users: ["alice", "bob"], groups: ["인사팀"] }));
      }
      if (url.endsWith("/access") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as DocumentAccess;
        // 폴더 밖 문서라 서버가 돌려주는 실제 적용 범위도 자기 범위와 같다.
        current = { ...current, visibility: body.visibility, effective_visibility: body.visibility };
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

    // 열람 범위는 머리글 메타 한 줄이 보인다 — 저장하면 그 줄이 새 범위로 바뀐다.
    await waitFor(() => expect(screen.getByTestId("document-header-meta")).toHaveTextContent("제한"));
    expect(screen.getByTestId("document-header-meta")).not.toHaveTextContent("조직 공개");
  });

  it("소유자가 아니면 패널을 보이지 않고 열람 범위를 조회하지도 않는다", async () => {
    const fetchMock = stubAccessFetch("bob");
    await renderPage();

    expect(screen.queryByRole("heading", { name: "열람 범위" })).not.toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url.endsWith("/access"))).toBe(false);
    expect(urls).not.toContain("/api/principals");
    expect(screen.queryByRole("button", { name: "소유자 이전" })).not.toBeInTheDocument();
    expect(urls).not.toContain("/api/shares");
  });
});

// 답변 인용 링크 `/documents/{id}?version=&chunk=` (ADR-043 결정 3, #96 b).
describe("문서 상세 페이지의 인용 위치", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    navigation.searchParams = new URLSearchParams();
  });

  // 검색 화면의 인용 링크는 클라이언트 이동이다 — Next는 새 화면을 그린 뒤에 주소창을 바꾸므로
  // window.location은 첫 렌더에 아직 /search다. 라우터의 검색 파라미터를 읽어야 한다(실사용에서 잡힘).
  it("주소의 version·chunk로 그 버전을 펼치고 대목 위치를 묻는다", async () => {
    window.history.replaceState(null, "", "/search");
    navigation.searchParams = new URLSearchParams("version=1&chunk=0");
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: ["alice", "lee"], groups: [] }));
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      if (url.includes("/versions/1")) {
        return Promise.resolve(jsonResponse({
          version: 1, content: "처음 근거", created_at: "2026-08-05T10:00:00Z",
          passage_start: 0, passage_end: 5,
        }));
      }
      return Promise.resolve(jsonResponse({
        ...detail,
        versions: [
          { version: 1, created_at: "2026-08-05T10:00:00Z", author: null, author_via: null },
          { version: 2, created_at: "2026-08-05T11:00:00Z", author: null, author_via: null },
        ],
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    Element.prototype.scrollIntoView = vi.fn();

    await renderPage();

    expect(await screen.findByText("처음 근거", { selector: "mark" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain(
      "/api/documents/document-1/versions/1?chunk=0",
    );
  });
});

describe("볼 수 없는 폴더 안 자기 문서", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("폴더 이름 없이 그 사실을 알리고 열람 범위 패널도 폴더 안으로 다룬다", async () => {
    const access: DocumentAccess = {
      visibility: "private", users: [], groups: [], follows_folder: true,
      folder: null, folder_scope: null, hidden_folder: true,
    };
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: [], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse(access));
      return Promise.resolve(jsonResponse({ ...detail, hidden_folder: true }));
    }));

    await renderPage();

    expect(await screen.findByText("볼 수 없는 폴더")).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "폴더 범위 따름(볼 수 없는 폴더)" })).toBeChecked();
  });
});

it("소유자 상세에는 소유자 이전을 표시한다", async () => {
  stubFetch(() => jsonResponse(detail));
  render(<AuthProvider><DocumentDetailView /></AuthProvider>);
  expect(await screen.findByRole("button", { name: "소유자 이전" })).toBeInTheDocument();
});

describe("목록으로 돌아가기", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("마지막으로 본 목록의 조건과 쪽으로 돌아간다", async () => {
    rememberListHref("/documents?sort=title&page=2");
    stubFetch(() => jsonResponse(detail));
    await renderPage();
    expect(screen.getByRole("link", { name: "← 문서 목록" })).toHaveAttribute("href", "/documents?sort=title&page=2");
  });

  it("목록을 거치지 않고 왔으면 첫 목록으로 간다", async () => {
    stubFetch(() => jsonResponse(detail));
    await renderPage();
    expect(screen.getByRole("link", { name: "← 문서 목록" })).toHaveAttribute("href", "/documents");
  });
});

// D9 — 왼쪽 본문 열·오른쪽 기둥·접힌 「문서 관리」.
describe("문서 상세의 2단 배치", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubLayoutFetch(username: string | null, document: DocumentDetail = detail) {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/me") {
        return Promise.resolve(jsonResponse(
          username === null
            ? { authenticated: false, username: null, is_admin: false }
            : { authenticated: true, username, is_admin: false },
        ));
      }
      if (url.endsWith("/links")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([{ document_id: "source-1", title: "출발 문서" }]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: ["alice", "bob"], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      return Promise.resolve(jsonResponse(document));
    }));
  }

  it("본문 열에는 텍스트·백링크·버전 이력을, 오른쪽 기둥에는 폴더·관련 문서·원본·태그·문서 정보를 둔다", async () => {
    stubLayoutFetch("alice");
    await renderPage();
    await screen.findByRole("link", { name: "출발 문서" });

    const columns = screen.getByTestId("document-columns");
    const side = within(columns).getByTestId("document-side");
    // 버전 이력은 왼쪽 카드의 「버전 이력」 탭 안에 있다.
    fireEvent.click(within(columns).getByRole("tab", { name: /버전 이력/ }));
    expect(within(side).queryByRole("tab")).not.toBeInTheDocument();
    for (const name of ["텍스트 버전 이력"]) {
      const heading = within(columns).getByRole("heading", { name });
      expect(side).not.toContainElement(heading);
    }
    fireEvent.click(within(columns).getByRole("tab", { name: "본문" }));
    for (const name of ["추출 텍스트", "이 문서를 가리키는 문서"]) {
      const heading = within(columns).getByRole("heading", { name });
      expect(side).not.toContainElement(heading);
    }
    for (const name of ["관련 문서", "원본 파일", "태그", "태그 추천", "문서 정보"]) {
      expect(within(side).getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(within(side).getByLabelText("폴더")).toBeInTheDocument();
    // 제목·메타 줄은 2단 위에 있다.
    expect(columns).not.toContainElement(screen.getByRole("heading", { level: 1 }));
  });

  it("관리 동작은 접힌 「문서 관리」 안에 두고, 소유자에게만 열람 범위·소유자 이전을 넣는다", async () => {
    stubLayoutFetch("alice");
    await renderPage();

    const manage = screen.getByText("문서 관리", { selector: "summary" }).closest("details");
    expect(manage).not.toBeNull();
    expect(manage).not.toHaveAttribute("open");
    expect(within(manage!).getByRole("button", { name: "삭제", hidden: true })).toBeInTheDocument();
    expect(within(manage!).getByRole("heading", { name: "열람 범위", hidden: true })).toBeInTheDocument();
    expect(within(manage!).getByRole("button", { name: "소유자 이전", hidden: true })).toBeInTheDocument();
    // TC 245의 문서 정보와 폴더 옮기기는 접힌 곳에 숨기지 않는다.
    expect(manage).not.toContainElement(screen.getByRole("heading", { name: "문서 정보" }));
    expect(manage).not.toContainElement(screen.getByLabelText("폴더"));
    expect(manage).not.toContainElement(screen.getByTestId("document-columns"));
  });

  it("「문서 관리」는 긴 본문 아래가 아니라 오른쪽 기둥 맨 위에 둔다", async () => {
    stubLayoutFetch("alice");
    await renderPage();

    const side = screen.getByTestId("document-side");
    const manage = screen.getByText("문서 관리", { selector: "summary" }).closest("details");
    expect(side).toContainElement(manage);
    expect(side.firstElementChild).toBe(manage);
  });

  it("임베딩 오류 문서는 재임베딩이 보이도록 「문서 관리」를 펼쳐 둔다", async () => {
    stubLayoutFetch("alice", { ...detail, embedding_status: "error" });
    await renderPage();

    const manage = screen.getByText("문서 관리", { selector: "summary" }).closest("details");
    expect(manage).toHaveAttribute("open");
    expect(within(manage!).getByRole("button", { name: "재임베딩" })).toBeInTheDocument();
  });

  it("소유자가 아니면 「문서 관리」에 열람 범위·소유자 이전이 없다", async () => {
    stubLayoutFetch("bob");
    await renderPage();

    const manage = screen.getByText("문서 관리", { selector: "summary" }).closest("details");
    expect(within(manage!).getByRole("button", { name: "삭제", hidden: true })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "열람 범위", hidden: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "소유자 이전", hidden: true })).not.toBeInTheDocument();
  });

  it("로그인하지 않았으면 「문서 관리」도 태그 편집도 없다", async () => {
    stubLayoutFetch(null);
    await act(async () => {
      render(<AuthProvider><DocumentDetailView /></AuthProvider>);
    });
    await screen.findByRole("link", { name: "출발 문서" });

    expect(screen.queryByText("문서 관리")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "태그" })).not.toBeInTheDocument();
    expect(within(screen.getByTestId("document-side")).getByRole("heading", { name: "문서 정보" })).toBeInTheDocument();
  });
});

// #293 — 왼쪽 카드를 「본문 · 버전 이력 · 활동 기록」 탭으로 나눈다.
describe("문서 상세의 탭", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    navigation.searchParams = new URLSearchParams();
    navigation.replace.mockReset();
  });

  const versioned: DocumentDetail = {
    ...detail,
    versions: [
      { version: 1, created_at: "2026-08-05T10:00:00Z", author: null, author_via: null },
      { version: 2, created_at: "2026-08-05T11:00:00Z", author: "alice", author_via: null },
    ],
  };

  function stubTabFetch(authenticated = true) {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/me") {
        return Promise.resolve(jsonResponse(
          authenticated
            ? { authenticated: true, username: "alice", is_admin: false }
            : { authenticated: false, username: null, is_admin: false },
        ));
      }
      if (url.endsWith("/links") || url.endsWith("/backlinks")) return Promise.resolve(jsonResponse([]));
      if (url.endsWith("/related")) return Promise.resolve(jsonResponse(related));
      if (url.endsWith("/tag-suggestions")) return Promise.resolve(jsonResponse(suggestions));
      if (url === "/api/shares") return Promise.resolve(jsonResponse([]));
      if (url === "/api/folders") return Promise.resolve(jsonResponse([]));
      if (url === "/api/principals") return Promise.resolve(jsonResponse({ users: ["alice"], groups: [] }));
      if (url.endsWith("/access")) return Promise.resolve(jsonResponse({ visibility: "public", users: [], groups: [] }));
      if (url.includes("/activity")) return Promise.resolve(jsonResponse({ items: [], next_before_id: null }));
      if (url.includes("/versions/1")) {
        return Promise.resolve(jsonResponse({
          version: 1, content: "처음 근거", created_at: "2026-08-05T10:00:00Z",
          passage_start: 0, passage_end: 5,
        }));
      }
      return Promise.resolve(jsonResponse(versioned));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function activityCalls(fetchMock: ReturnType<typeof stubTabFetch>): number {
    return fetchMock.mock.calls.filter(([url]) => String(url).includes("/activity")).length;
  }

  async function renderAnonymous(): Promise<void> {
    await act(async () => {
      render(<AuthProvider><DocumentDetailView /></AuthProvider>);
    });
    await screen.findByRole("tab", { name: "본문" });
  }

  it("기본 탭은 본문이고 문서 텍스트가 펼치지 않고 바로 보인다", async () => {
    stubTabFetch();
    await renderPage();

    expect(screen.getByRole("tab", { name: "본문" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /버전 이력/ })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText("추출된 텍스트")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "텍스트 버전 이력" })).not.toBeInTheDocument();
  });

  it("「버전 이력」 탭은 버전 수를 달고, 누르면 버전 이력과 각 버전이 보이며 주소에 tab을 남긴다", async () => {
    navigation.searchParams = new URLSearchParams("from=list");
    stubTabFetch();
    await renderPage();

    const tab = screen.getByRole("tab", { name: /버전 이력/ });
    expect(tab).toHaveTextContent("2");
    fireEvent.click(tab);

    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("추출된 텍스트")).not.toBeVisible();
    const panel = screen.getByRole("tabpanel", { name: /버전 이력/ });
    expect(within(panel).getByRole("heading", { name: "텍스트 버전 이력" })).toBeVisible();
    expect(within(panel).getByText(/v1/)).toBeInTheDocument();
    expect(within(panel).getByText(/v2/)).toBeInTheDocument();
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/documents/document-1?from=list&tab=versions", { scroll: false },
    );

    fireEvent.click(screen.getByRole("tab", { name: "본문" }));
    expect(navigation.replace).toHaveBeenLastCalledWith("/documents/document-1?from=list", { scroll: false });
  });

  it("주소에 tab=versions가 있으면 버전 이력 탭으로 연다", async () => {
    navigation.searchParams = new URLSearchParams("tab=versions");
    stubTabFetch();
    await renderPage();

    expect(screen.getByRole("tab", { name: /버전 이력/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "텍스트 버전 이력" })).toBeVisible();
  });

  it("인용 위치가 있으면 tab보다 우선해 버전 이력 탭에서 그 버전을 펼치고 대목으로 스크롤한다", async () => {
    navigation.searchParams = new URLSearchParams("tab=activity&version=1&chunk=0");
    stubTabFetch();
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await renderPage();

    expect(screen.getByRole("tab", { name: /버전 이력/ })).toHaveAttribute("aria-selected", "true");
    const mark = await screen.findByText("처음 근거", { selector: "mark" });
    expect(mark).toBeVisible();
    await waitFor(() => expect(scroll).toHaveBeenCalled());
  });

  it("인용 링크로 들어온 뒤에도 탭을 고르면 주소가 바뀌어도 그 탭에 머문다", async () => {
    navigation.searchParams = new URLSearchParams("version=1&chunk=0");
    stubTabFetch();
    Element.prototype.scrollIntoView = vi.fn();
    const view = (): React.ReactElement => <AuthProvider><DocumentDetailView /></AuthProvider>;
    let rerender: (ui: React.ReactElement) => void = () => {};
    await act(async () => {
      rerender = render(view()).rerender;
    });
    await screen.findByRole("tab", { name: "본문" });
    // 실제 라우터처럼 replace가 주소를 바꾸면 useSearchParams가 새 객체를 돌려준다.
    navigation.replace.mockImplementation((href: string) => {
      navigation.searchParams = new URLSearchParams(href.split("?")[1] ?? "");
      rerender(view());
    });
    expect(screen.getByRole("tab", { name: /버전 이력/ })).toHaveAttribute("aria-selected", "true");

    act(() => fireEvent.click(screen.getByRole("tab", { name: "활동 기록" })));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/documents/document-1?version=1&chunk=0&tab=activity", { scroll: false },
    );
    expect(screen.getByRole("tab", { name: "활동 기록" })).toHaveAttribute("aria-selected", "true");

    act(() => fireEvent.click(screen.getByRole("tab", { name: "본문" })));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/documents/document-1?version=1&chunk=0", { scroll: false },
    );
    expect(screen.getByRole("tab", { name: "본문" })).toHaveAttribute("aria-selected", "true");
  });

  it("「활동 기록」은 처음 열 때 한 번만 불러오고, 탭을 오가도 다시 부르지 않는다", async () => {
    const fetchMock = stubTabFetch();
    await renderPage();

    expect(activityCalls(fetchMock)).toBe(0);
    fireEvent.click(screen.getByRole("tab", { name: "활동 기록" }));
    expect(await screen.findByText("아직 기록이 없습니다.")).toBeVisible();
    expect(activityCalls(fetchMock)).toBe(1);

    fireEvent.click(screen.getByRole("tab", { name: "본문" }));
    fireEvent.click(screen.getByRole("tab", { name: "활동 기록" }));
    expect(screen.getByText("아직 기록이 없습니다.")).toBeVisible();
    expect(activityCalls(fetchMock)).toBe(1);
  });

  it("로그인하지 않았으면 활동 기록 탭이 없다", async () => {
    navigation.searchParams = new URLSearchParams("tab=activity");
    const fetchMock = stubTabFetch(false);
    await renderAnonymous();

    expect(screen.queryByRole("tab", { name: "활동 기록" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "본문" })).toHaveAttribute("aria-selected", "true");
    expect(activityCalls(fetchMock)).toBe(0);
  });

  it("편집 중 탭을 바꿨다 돌아와도 편집 내용이 남는다", async () => {
    stubTabFetch();
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    fireEvent.change(screen.getByRole("textbox", { name: "추출 텍스트" }), {
      target: { value: "고치던 내용" },
    });
    fireEvent.click(screen.getByRole("tab", { name: /버전 이력/ }));
    fireEvent.click(screen.getByRole("tab", { name: "본문" }));

    expect(screen.getByRole("textbox", { name: "추출 텍스트" })).toHaveValue("고치던 내용");
  });
});
