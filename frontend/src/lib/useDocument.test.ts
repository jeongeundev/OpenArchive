import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetail } from "./types";
import { useDocument } from "./useDocument";

const document: DocumentDetail = {
  folder: null,
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  content: "추출된 텍스트",
  version: 1,
  owner_id: "alice",
  visibility: "public",
  tags: ["OpenSQL"],
  embedding_status: "pending",
  extraction_status: "done",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [{ version: 1, created_at: "2026-08-05T10:00:00Z" }],
  files: [],
  chunk_count: 0,
  chunk_version: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function flushRequest(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useDocument", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("pending 문서는 2초 뒤 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(document));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocument(document.id));
    await flushRequest();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("ready 문서는 다시 조회하지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...document, embedding_status: "ready" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocument(document.id));
    await flushRequest();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("언마운트하면 예약된 조회를 취소한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(document));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useDocument(document.id));
    await flushRequest();
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("404를 사용자용 문구로 표시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 404)));

    const { result } = renderHook(() => useDocument("missing"));
    await flushRequest();

    expect(result.current.error).toBe("문서를 찾을 수 없습니다.");
  });

  // 경로가 UUID가 아니면 FastAPI의 경로 파라미터 검증이 422를 내고, 그 `detail`은
  // 문자열이 아니라 validation error 배열이라 `api.ts`의 폴백(상태 코드가 박힌 문구)에
  // 걸린다. 사용자에게 없는 UUID와 형식이 틀린 값은 같은 사실이므로 같은 문구여야 한다.
  it("경로 형식이 틀린 값(422)도 없는 문서와 같은 문구로 표시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(
      { detail: [{ type: "uuid_parsing", loc: ["path", "document_id"], msg: "Input should be a valid UUID" }] },
      422,
    )));

    const { result } = renderHook(() => useDocument("abc"));
    await flushRequest();

    expect(result.current.error).toBe("문서를 찾을 수 없습니다.");
  });

  it("텍스트 인식 중인 문서는 임베딩 상태와 무관하게 다시 조회한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...document, embedding_status: "ready", extraction_status: "pending" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocument(document.id));
    await flushRequest();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("텍스트 인식에 실패한 문서는 다시 조회하지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...document, embedding_status: "ready", extraction_status: "failed" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useDocument(document.id));
    await flushRequest();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

// 응답하지 않는 서버 — 화면을 떠날 때 요청이 취소되는지만 본다.
function pendingFetch() {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}));
}

function expectAllAborted(fetchMock: ReturnType<typeof pendingFetch>): void {
  expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
  for (const [, init] of fetchMock.mock.calls) expect(init?.signal?.aborted).toBe(true);
}

describe("useDocument 취소", () => {
  it("언마운트하면 진행 중인 조회를 취소한다", () => {
    const fetchMock = pendingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useDocument("document-1"));
    unmount();

    expectAllAborted(fetchMock);
    vi.unstubAllGlobals();
  });
});

describe("useDocument StrictMode", () => {
  // 개발 모드의 StrictMode는 마운트 → 정리 → 재마운트를 한다. 정리가 첫 조회를 취소해도
  // 재마운트한 쪽의 조회가 막히면 문서가 끝내 뜨지 않는다(폴링도 없다).
  it("정리 후 재마운트해도 문서를 불러온다", async () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
          queueMicrotask(() => resolve(jsonResponse(document)));
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useDocument("document-1"), { wrapper: StrictMode });
    await flushRequest();

    expect(result.current.document).toEqual(document);
    expect(result.current.error).toBeNull();
    vi.unstubAllGlobals();
  });
});
