import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  changePassword,
  createToken,
  deleteDocument,
  editDocument,
  getAuthStatus,
  getDocument,
  isRetrying,
  listDocuments,
  listTokens,
  revokeToken,
  search,
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
