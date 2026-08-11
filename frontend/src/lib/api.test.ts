import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteDocument,
  editDocument,
  getAuthStatus,
  listDocuments,
  search,
  updateTags,
  uploadDocument,
} from "./api";

describe("API cookie session", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses same-origin credentials without a user identity header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ authenticated: false, username: null, is_admin: false }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getAuthStatus();

    expect(fetchMock.mock.calls[0][1]?.credentials).toBe("same-origin");
  });
});

describe("API responses", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("preserves the current version from a 409 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: "다른 곳에서 수정되었습니다.", current_version: 3 }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await editDocument("doc-1", { content: "수정", version: 2 }).catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, currentVersion: 3 });
  });

  it("uses the backend detail from a 400 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "로그인이 필요합니다." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const error = await editDocument("doc-1", { content: "수정", version: 1 }).catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, detail: "로그인이 필요합니다." });
  });

  it("uploads multipart data with each tag as a repeated field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["text"], "notes.txt", { type: "text/plain" });

    await uploadDocument({
      file,
      title: "메모",
      tags: ["OpenSQL", "검색"],
      visibility: "private",
    });

    const body = fetchMock.mock.calls[0][1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).getAll("tags")).toEqual(["OpenSQL", "검색"]);
    expect((body as FormData).get("file")).toBe(file);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has("Content-Type")).toBe(false);
  });

  it("adds the embedding status filter to the document list query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);

    await listDocuments({ status: "error" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents?status=error");
  });

  it("omits the tag filter from the search body when no tag is entered", async () => {
    // 백엔드 SQL은 "필터 없음"을 NULL로만 표현한다. 빈 배열을 보내면
    // d.tags && '{}' 가 항상 거짓이라 결과가 0건이 된다.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ items: [], sql: "" })));
    vi.stubGlobal("fetch", fetchMock);

    await search({ query: "OpenSQL", tags: [], contentType: null, k: 10 });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("tags");
  });

  it("returns normally for a 204 delete response without parsing JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(deleteDocument("doc-1")).resolves.toBeUndefined();
  });

  it("replaces the full tag list through the tag endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    await updateTags("doc/1", ["OpenSQL", "pgvector"]);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc%2F1/tags");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      tags: ["OpenSQL", "pgvector"],
    });
  });
});
