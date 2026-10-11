import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  transferDocumentOwner, transferFolderOwner, deleteUser,
  originalPreviewUrl,
  listFolders, createFolder, renameFolder, deleteFolder, getFolderAccess, setFolderAccess, moveDocument,
  addGroupMember,
  addShareDocument,
  addShareFolder,
  ask,
  changePassword,
  createGroup,
  createShare,
  createShareToken,
  createToken,
  deleteDocument,
  listTrash,
  purgeDocument,
  restoreDocument,
  deleteGroup,
  deleteShare,
  editDocument,
  getAuthStatus,
  getDocument,
  getDocumentAccess,
  getDocumentProgress,
  getDocumentVersion,
  isRetrying,
  listDocuments,
  countDocuments,
  listDocumentTags,
  listAudit,
  listGroups,
  listPrincipals,
  listShares,
  listTokens,
  removeGroupMember,
  removeShareDocument,
  removeShareFolder,
  revokeShareToken,
  revokeToken,
  search,
  setDocumentAccess,
  subscribeRetrying,
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

  it("appends each grantee as a repeated field when uploading", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await uploadDocument({
      file: new File(["text"], "notes.txt"),
      tags: [],
      visibility: "private",
      grantUsers: ["bob", "carol"],
      grantGroups: ["인사팀"],
    });

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.getAll("grant_users")).toEqual(["bob", "carol"]);
    expect(body.getAll("grant_groups")).toEqual(["인사팀"]);
  });

  it("omits grantee fields when none are given", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await uploadDocument({ file: new File(["text"], "notes.txt"), tags: [], visibility: "public" });

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.has("grant_users")).toBe(false);
    expect(body.has("grant_groups")).toBe(false);
  });

  it("adds the embedding status filter to the document list query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);

    await listDocuments({ status: "error" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents?status=error");
  });

  it("adds the page window to the document list query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);

    await listDocuments({ limit: 50, offset: 100 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents?limit=50&offset=100");
  });

  it("forwards finder filters to list and count without pagination on count", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(new Response(url.includes("/count") ? '{"total":7}' : "[]")),
    );
    vi.stubGlobal("fetch", fetchMock);
    const params = { q: "출장", contentType: "hwp", tag: "보안", sort: "title", status: "ready", limit: 50, offset: 0 } as const;
    await listDocuments(params);
    await expect(countDocuments(params)).resolves.toBe(7);
    const list = new URL(fetchMock.mock.calls[0][0], "http://localhost");
    const count = new URL(fetchMock.mock.calls[1][0], "http://localhost");
    expect(Object.fromEntries(list.searchParams)).toEqual({ q: "출장", content_type: "hwp", tag: "보안", sort: "title", status: "ready", limit: "50", offset: "0" });
    expect(count.pathname).toBe("/api/documents/count");
    expect(Object.fromEntries(count.searchParams)).toEqual({ q: "출장", content_type: "hwp", tag: "보안", status: "ready" });
  });

  it("omits blank finder conditions and reads visible tags", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve(new Response(url.includes("/count") ? '{"total":0}' : '[]')),
    );
    vi.stubGlobal("fetch", fetchMock);
    await listDocuments({ q: "  ", tag: "" });
    await countDocuments({ q: "", tag: "  " });
    await expect(listDocumentTags()).resolves.toEqual([]);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/api/documents", "/api/documents/count", "/api/documents/tags"]);
  });

  it("reads pipeline progress from its own path", async () => {
    const progress = {
      extracting: 0,
      extraction_failed: 0,
      pending: 1,
      processing: 0,
      ready: 2,
      error: 0,
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(progress)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDocumentProgress()).resolves.toEqual(progress);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/progress");
  });

  it("omits the tag filter from the search body when no tag is entered", async () => {
    // 백엔드 SQL은 "필터 없음"을 NULL로만 표현한다. 빈 배열을 보내면
    // d.tags && '{}' 가 항상 거짓이라 결과가 0건이 된다.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ items: [], sql: "" })));
    vi.stubGlobal("fetch", fetchMock);

    await search({ query: "OpenSQL", tags: [], contentType: null, folderId: null, k: 10 });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).not.toHaveProperty("tags");
  });

  it("asks with the same body as search, omitting empty filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "disabled", answer: null, detail: null, sources: [], items: [] })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await ask({ query: "OpenSQL", tags: [], contentType: "md", k: 5 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/ask");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      query: "OpenSQL",
      content_type: "md",
      k: 5,
    });
  });

  // 답변 인용이 「그 버전의 그 자리」로 가려면 서버가 그 판의 대목 위치를 계산해야 한다 (#96 b).
  it("asks the version endpoint for a chunk's position when given one", async () => {
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);

    await getDocumentVersion("doc/1", 3, undefined, 2);
    await getDocumentVersion("doc/1", 3);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc%2F1/versions/3?chunk=2");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/doc%2F1/versions/3");
  });

  it("lists, restores and permanently deletes trashed documents", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      (url) => Promise.resolve(
        url.endsWith("/restore") ? new Response("{}")
          : url === "/api/documents/trash" ? new Response("[]")
          : new Response(null, { status: 204 }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listTrash()).resolves.toEqual([]);
    await restoreDocument("doc/1");
    await expect(purgeDocument("doc/1")).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/trash");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/doc%2F1/restore");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/documents/doc%2F1?permanent=true");
    expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
  });

  it("returns normally for a 204 delete response without parsing JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(deleteDocument("doc-1")).resolves.toBeUndefined();
  });

  it("issues a token with its name and scope and returns the one-time plaintext", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "token-1",
          name: "CLI",
          scope: "read_write",
          created_at: "2026-08-21T00:00:00Z",
          token: "plaintext-once",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const issued = await createToken({ name: "CLI", scope: "read_write" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/tokens");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      name: "CLI",
      scope: "read_write",
    });
    expect(issued.token).toBe("plaintext-once");
  });

  it("만료일을 정해 발급하면 body에 expires_at을 싣고, 없으면 생략한다", async () => {
    const created = {
      id: "token-1",
      name: "CLI",
      scope: "read",
      created_at: "2026-10-08T00:00:00Z",
      expires_at: "2026-11-01T00:00:00Z",
      last_used_at: null,
      expired: false,
      token: "plaintext-once",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await createToken({ name: "CLI", scope: "read", expires_at: "2026-11-01T00:00:00Z" });
    await createToken({ name: "CLI", scope: "read", expires_at: null });
    await createShareToken("s1", "B사 연동", "2026-11-01T00:00:00Z");

    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      name: "CLI",
      scope: "read",
      expires_at: "2026-11-01T00:00:00Z",
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      name: "CLI",
      scope: "read",
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      name: "B사 연동",
      expires_at: "2026-11-01T00:00:00Z",
    });
  });

  it("lists tokens and revokes one by id", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("[]"))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await listTokens();
    await expect(revokeToken("token/1")).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/tokens");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/tokens/token%2F1");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("sends both passwords to the password endpoint as a PUT", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ authenticated: false, username: null, is_admin: false }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const status = await changePassword("old-secret", "new-secret");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/password");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      current_password: "old-secret",
      new_password: "new-secret",
    });
    expect(status.authenticated).toBe(false);
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

// ADR-048 결정 4 — 1초 시작, 상한 8초, 전체 지터, 총 60초. 읽기와 멱등키가 있는 문서 생성만.
describe("API retry on temporary unavailability", () => {
  function unavailable(): Response {
    return new Response(
      JSON.stringify({ detail: "일시적으로 요청을 처리할 수 없습니다. 잠시 후 다시 시도하세요." }),
      { status: 503, headers: { "Content-Type": "application/json", "Retry-After": "1" } },
    );
  }

  function ok(body: unknown): Response {
    return new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json" },
    });
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useFakeTimers();
    // HTTP 날짜는 초 단위라 시계를 정각에 맞춘다.
    vi.setSystemTime(new Date("2026-09-27T00:00:00Z"));
    // 전체 지터의 상한 — 대기 간격이 결정적이 된다.
    vi.spyOn(Math, "random").mockReturnValue(0.999999);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("waits out a 503 on a read with exponential backoff", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    // 지터가 상한의 절반을 고르면 백오프는 500ms, 1000ms다. 첫 간격은 Retry-After(1초)보다
    // 짧아 1초로 올라가고, 둘째는 그대로 1초다 — 경계가 ms 단위로 딱 떨어진다.
    vi.spyOn(Math, "random").mockReturnValue(0.5);

    const result = listDocuments();
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);

    await expect(result).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  // RFC 9110 §10.2.3 — Retry-After는 초 수 또는 HTTP 날짜다. 그보다 일찍 다시 보내지 않는다.
  it.each([
    ["delay-seconds", () => "5"],
    ["HTTP-date", () => new Date(Date.now() + 5000).toUTCString()],
  ])("does not retry before Retry-After given as %s", async (_form, retryAfter) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("", { status: 503, headers: { "Retry-After": retryAfter() } }),
      )
      .mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);

    const result = listDocuments();
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);

    await expect(result).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses the jittered backoff alone after a network error, which carries no Retry-After", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(Math, "random").mockReturnValue(0.5);

    const result = listDocuments();
    await vi.advanceTimersByTimeAsync(499);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);

    await expect(result).resolves.toEqual([]);
  });

  it("gives up at once when Retry-After is beyond the one-minute budget", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("", { status: 503, headers: { "Retry-After": "120" } }));
    vi.stubGlobal("fetch", fetchMock);

    const error = await listDocuments().catch((reason: unknown) => reason);

    expect((error as ApiError).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries search, which is a read sent as POST, after a network error", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = search({ query: "정합성" });
    await vi.advanceTimersByTimeAsync(1000);

    await expect(result).resolves.toEqual({ items: [] });
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ query: "정합성" }));
  });

  // 서버는 DB 단계(생성 전)에서만 503을 낸다 — 다시 보내도 생성이 두 번 돌지 않는다 (ADR-043).
  it("retries ask, which is a read sent as POST, after a 503", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(ok({ status: "answered", sources: [], items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = ask({ query: "정합성" });
    await vi.advanceTimersByTimeAsync(2000);

    await expect(result).resolves.toMatchObject({ status: "answered" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  // ADR-047 — 첫 시도가 커밋된 채 응답만 잃었어도, 같은 키로 다시 보내면 처음 문서가 온다.
  it("retries an upload with the same Idempotency-Key on every attempt", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unavailable())
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok({ id: "document-1" }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.txt");

    const result = uploadDocument({ file, tags: [], visibility: "public" });
    await vi.advanceTimersByTimeAsync(3000);

    await expect(result).resolves.toEqual({ id: "document-1" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const keys = fetchMock.mock.calls.map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    );
    expect(keys[0]).toMatch(/^[0-9a-f]{32}$/);
    expect(new Set(keys)).toEqual(new Set([keys[0]]));
    expect((fetchMock.mock.calls[2][1]?.body as FormData).get("file")).toBe(file);
  });

  it("gives every upload its own Idempotency-Key", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(ok({})));
    vi.stubGlobal("fetch", fetchMock);
    const upload = () =>
      uploadDocument({ file: new File(["x"], "a.txt"), tags: [], visibility: "public" });

    await upload();
    await upload();

    const [first, second] = fetchMock.mock.calls.map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    );
    expect(first).not.toBe(second);
  });

  it("does not retry other writes — only document creation honours the key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(unavailable());
    vi.stubGlobal("fetch", fetchMock);

    const error = await updateTags("document-1", ["태그"]).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has("Idempotency-Key")).toBe(false);
  });

  it("does not retry a 500 — it will not pass with time", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listDocuments()).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("caps the wait at 8 seconds and gives up after a minute", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(unavailable()));
    vi.stubGlobal("fetch", fetchMock);

    const result = listDocuments().catch((reason: unknown) => reason);
    // 1+2+4+8×6 = 55초까지 기다린 뒤, 다음 8초는 60초를 넘기므로 멈춘다.
    await vi.advanceTimersByTimeAsync(55_000);
    const error = await result;

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });

  // 화면을 떠난 요청은 응답을 쓸 곳이 없다. 기다리던 재시도까지 멈춰야 서버 부하와
  // 재시도 안내가 남지 않는다(React "Fetching data" 정리 함수 관례).
  it("stops the backoff when the caller aborts while waiting", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(unavailable()));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const result = listDocuments(undefined, controller.signal).catch((reason: unknown) => reason);
    await vi.advanceTimersByTimeAsync(0);
    expect(isRetrying()).toBe(true);
    controller.abort();
    const error = await result;
    await vi.advanceTimersByTimeAsync(60_000);

    expect((error as Error).name).toBe("AbortError");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(isRetrying()).toBe(false);
  });

  it("hands the caller's signal to fetch so an in-flight read is cancelled too", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(ok([])));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getDocument("document-1", controller.signal);
    await search({ query: "정합성" }, controller.signal);

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(fetchMock.mock.calls[1][1]?.signal).toBe(controller.signal);
  });

  it("reports that a retry is in progress until the request settles", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(ok([]));
    vi.stubGlobal("fetch", fetchMock);
    const changes: boolean[] = [];
    const unsubscribe = subscribeRetrying(() => changes.push(isRetrying()));

    const result = listDocuments();
    await vi.advanceTimersByTimeAsync(0);
    expect(isRetrying()).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    await result;

    expect(isRetrying()).toBe(false);
    expect(changes).toEqual([true, false]);
    unsubscribe();
  });
});

describe("groups and document access", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function stubFetch(body: string | null, status = 200) {
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("lists audit entries with only the given filters in the query", async () => {
    const fetchMock = stubFetch(JSON.stringify({ items: [], next_before_id: null }));

    const page = await listAudit({ actor: "bob", action: "text_updated", beforeId: 42, limit: 50 });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/admin/audit?actor=bob&action=text_updated&limit=50&before_id=42",
    );
    expect(page).toEqual({ items: [], next_before_id: null });
  });

  it("omits empty audit filters", async () => {
    const fetchMock = stubFetch(JSON.stringify({ items: [], next_before_id: null }));

    await listAudit({ actor: "", action: "" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/audit");
  });

  it("lists groups from the admin path", async () => {
    const fetchMock = stubFetch("[]");

    await listGroups();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/groups");
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  it("creates a group with its name in a JSON body", async () => {
    const fetchMock = stubFetch(
      JSON.stringify({ id: "g1", name: "인사팀", created_at: "2026-10-01T00:00:00Z", members: [] }),
      201,
    );

    await createGroup("인사팀");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/admin/groups");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ name: "인사팀" });
  });

  it("deletes a group by id", async () => {
    const fetchMock = stubFetch(null, 204);

    await deleteGroup("g/1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/groups/g%2F1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("adds and removes a member with the username in the path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await addGroupMember("g1", "김 철수");
    await removeGroupMember("g1", "김 철수");

    const encoded = encodeURIComponent("김 철수");
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/admin/groups/g1/members/${encoded}`);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/admin/groups/g1/members/${encoded}`);
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("lists principals names", async () => {
    const fetchMock = stubFetch(JSON.stringify({ users: ["bob"], groups: ["인사팀"] }));

    await expect(listPrincipals()).resolves.toEqual({ users: ["bob"], groups: ["인사팀"] });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/principals");
  });

  it("reads a document's access settings", async () => {
    const access = { visibility: "private", users: ["bob"], groups: [] };
    const fetchMock = stubFetch(JSON.stringify(access));

    await expect(getDocumentAccess("doc-1")).resolves.toEqual(access);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/access");
  });

  it("replaces a document's access settings with PUT", async () => {
    const access = { visibility: "private" as const, users: ["bob"], groups: ["인사팀"] };
    const fetchMock = stubFetch(JSON.stringify(access));

    await setDocumentAccess("doc-1", access);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/documents/doc-1/access");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual(access);
  });

  it("does not retry an access change on 503", async () => {
    const fetchMock = stubFetch(JSON.stringify({ detail: "잠시 후" }), 503);

    await setDocumentAccess("doc-1", { visibility: "public", users: [], groups: [] }).catch(
      () => undefined,
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("shares", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function stubFetch(body: string | null, status = 200) {
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("lists own shares and passes the abort signal", async () => {
    const shares = [
      {
        id: "s1",
        name: "B사",
        created_at: "2026-10-02T00:00:00Z",
        documents: [{ id: "d1", title: "제품 설명서" }],
        tokens: [{ id: "t1", name: "B사 연동", scope: "read", created_at: "2026-10-02T00:00:00Z" }],
      },
    ];
    const fetchMock = stubFetch(JSON.stringify(shares));
    const controller = new AbortController();

    await expect(listShares(controller.signal)).resolves.toEqual(shares);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/shares");
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("creates a share with its name in a JSON body", async () => {
    const fetchMock = stubFetch(
      JSON.stringify({ id: "s1", name: "B사", created_at: "x", documents: [], tokens: [] }),
      201,
    );

    await createShare("B사");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/shares");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ name: "B사" });
  });

  it("deletes a share by id", async () => {
    const fetchMock = stubFetch(null, 204);

    await expect(deleteShare("s/1")).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/shares/s%2F1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("adds and removes a document with encoded path segments", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await addShareDocument("s/1", "d 1");
    await removeShareDocument("s/1", "d 1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/shares/s%2F1/documents/d%201");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/shares/s%2F1/documents/d%201");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("adds and removes a folder with encoded path segments", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await addShareFolder("s/1", "f 1");
    await removeShareFolder("s/1", "f 1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/shares/s%2F1/folders/f%201");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/shares/s%2F1/folders/f%201");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("issues a share token and returns the raw token once", async () => {
    const issued = {
      id: "t1",
      name: "B사 연동",
      scope: "read",
      created_at: "2026-10-02T00:00:00Z",
      token: "raw-secret",
    };
    const fetchMock = stubFetch(JSON.stringify(issued), 201);

    await expect(createShareToken("s1", "B사 연동")).resolves.toEqual(issued);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/shares/s1/tokens");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ name: "B사 연동" });
  });

  it("revokes a share token", async () => {
    const fetchMock = stubFetch(null, 204);

    await expect(revokeShareToken("s1", "t/1")).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/shares/s1/tokens/t%2F1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("surfaces the server detail as ApiError", async () => {
    stubFetch(JSON.stringify({ detail: "이미 존재하는 공유 이름입니다." }), 409);

    const error = await createShare("B사").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe("이미 존재하는 공유 이름입니다.");
  });

  it("does not retry a share change on 503", async () => {
    const fetchMock = stubFetch(JSON.stringify({ detail: "잠시 후" }), 503);

    await addShareDocument("s1", "d1").catch(() => undefined);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});


describe("folder client contracts", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("sends folder methods, encoded paths, bodies and signals", async () => {
    const fetchMock = vi.fn().mockImplementation((_url, init) => Promise.resolve(
      init.method === "DELETE" ? new Response(null, { status: 204 }) : new Response(JSON.stringify({ id: "f" })),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;
    const scope = { visibility: "private" as const, users: ["kim"], groups: ["사업팀"] };
    await listFolders(signal);
    await createFolder({ name: "채용", parentId: "parent" });
    await createFolder({ name: "인사" });
    await renameFolder("a/b", "RFP");
    await deleteFolder("a/b");
    await getFolderAccess("a/b", signal);
    await setFolderAccess("a/b", scope);
    await moveDocument("a/b", "f");
    await moveDocument("a/b", null);
    const calls = fetchMock.mock.calls;
    expect(calls.map(([url]) => url)).toEqual([
      "/api/folders", "/api/folders", "/api/folders", "/api/folders/a%2Fb", "/api/folders/a%2Fb",
      "/api/folders/a%2Fb/access", "/api/folders/a%2Fb/access", "/api/documents/a%2Fb/folder", "/api/documents/a%2Fb/folder",
    ]);
    expect(calls[0][1].signal).toBe(signal);
    expect(calls[5][1].signal).toBe(signal);
    for (const [index, method, body] of [
      [1, "POST", {name: "채용", parent_id: "parent"}], [2, "POST", {name: "인사"}],
      [3, "PATCH", {name: "RFP"}], [6, "PUT", scope],
      [7, "PUT", {folder_id: "f"}], [8, "PUT", {folder_id: null}],
    ] as const) {
      expect(calls[index][1].method).toBe(method);
      expect(new Headers(calls[index][1].headers).get("Content-Type")).toBe("application/json");
      expect(JSON.parse(calls[index][1].body)).toEqual(body);
    }
    expect(calls[4][1].method).toBe("DELETE");
  });
  it("passes folder filters to lists, counts, search and ask", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({total: 0}))));
    vi.stubGlobal("fetch", fetchMock);
    await listDocuments({folderId: "f"});
    await countDocuments({folderId: "f"});
    await search({query: "질문", folderId: "f"});
    await ask({query: "질문", folderId: "f"});
    await search({query: "질문", folderId: null});
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents?folder_id=f");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/count?folder_id=f");
    for (const index of [2, 3]) expect(JSON.parse(fetchMock.mock.calls[index][1].body)).toEqual({query: "질문", folder_id: "f"});
    expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({query: "질문"});
  });
  it("omits individual access fields from folder uploads", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);
    await uploadDocument({file: new File(["text"], "a.txt"), tags: ["tag"], visibility: "private", grantUsers: ["kim"], grantGroups: ["team"], folderId: "f"});
    const body = fetchMock.mock.calls[0][1].body as FormData;
    expect(body.get("folder_id")).toBe("f");
    for (const field of ["visibility", "grant_users", "grant_groups"]) expect(body.has(field)).toBe(false);
    expect(body.getAll("tags")).toEqual(["tag"]);
    expect(body.get("file")).toBeInstanceOf(File);
  });
  it("sends only the appropriate document access shape", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);
    const scope = {visibility: "private" as const, users: ["kim"], groups: []};
    await setDocumentAccess("d", {followsFolder: true});
    await setDocumentAccess("d", {followsFolder: false, ...scope});
    await setDocumentAccess("d", scope);
    expect(fetchMock.mock.calls.map(([, init]) => JSON.parse(init.body))).toEqual([
      {follows_folder: true}, {follows_folder: false, ...scope}, scope,
    ]);
  });
});

describe("원본 미리보기 경로와 형식", () => {
  it("문서 id를 인코딩한 판별 경로를 만든다", () => {
    expect(originalPreviewUrl("doc /한글", 2)).toBe(`/api/documents/${encodeURIComponent("doc /한글")}/files/2/preview`);
  });
});


describe("owner transfer requests", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("sends PUT owner bodies with session cookies", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ still_visible: true }))));
    vi.stubGlobal("fetch", fetchMock);
    await transferDocumentOwner("doc", "lee");
    await transferFolderOwner("folder", "lee");
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/documents/doc/owner", expect.objectContaining({ method: "PUT", body: JSON.stringify({ owner: "lee" }), credentials: "same-origin" }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/folders/folder/owner", expect.objectContaining({ method: "PUT", body: JSON.stringify({ owner: "lee" }) }));
  });
  it("encodes the optional deletion transfer target", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status: 204 })));
    vi.stubGlobal("fetch", fetchMock);
    await deleteUser("u", "lee & kim");
    expect(fetchMock).toHaveBeenCalledWith("/api/admin/users/u?transfer_to=lee%20%26%20kim", expect.objectContaining({ method: "DELETE" }));
  });
});
