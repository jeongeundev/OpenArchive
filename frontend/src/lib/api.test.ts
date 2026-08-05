import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteDocument,
  editDocument,
  listDocuments,
  uploadDocument,
} from "./api";
import { DEMO_USERS, setCurrentUser } from "./user";

describe("API user header", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends the selected user in X-User-Id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    setCurrentUser(DEMO_USERS[0]);

    await listDocuments();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("X-User-Id")).toBe(DEMO_USERS[0]);
  });

  it("omits X-User-Id for an anonymous user", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);

    await listDocuments();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.has("X-User-Id")).toBe(false);
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
        new Response(JSON.stringify({ detail: "X-User-Id 헤더가 필요합니다." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const error = await editDocument("doc-1", { content: "수정", version: 1 }).catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, detail: "X-User-Id 헤더가 필요합니다." });
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

  it("returns normally for a 204 delete response without parsing JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(deleteDocument("doc-1")).resolves.toBeUndefined();
  });
});
