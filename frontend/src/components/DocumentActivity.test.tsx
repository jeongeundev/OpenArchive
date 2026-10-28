// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentActivityEntry } from "@/lib/types";

import { DocumentActivity } from "./DocumentActivity";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const updated: DocumentActivityEntry = {
  id: 12,
  occurred_at: "2026-10-27T10:00:00Z",
  action: "text_updated",
  actor: "alice",
  actor_via: "session",
  detail: { version: 2 },
};

const ownerChanged: DocumentActivityEntry = {
  id: 11,
  occurred_at: "2026-10-27T09:00:00Z",
  action: "owner_changed",
  actor: null,
  actor_via: "worker",
  detail: { before: "alice", after: "bob" },
};

const created: DocumentActivityEntry = {
  id: 3,
  occurred_at: "2026-10-26T09:00:00Z",
  action: "document_created",
  actor: "carol",
  actor_via: null,
  detail: {},
};

describe("DocumentActivity", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("기록마다 동작·행위자·경로·설명을 보여 준다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [updated, ownerChanged], next_before_id: null }));
    vi.stubGlobal("fetch", fetchMock);

    render(<DocumentActivity documentId="doc/1" />);

    expect(await screen.findByText("텍스트 수정(v2)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "활동 기록" })).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("소유자 변경")).toBeInTheDocument();
    expect(screen.getByText("알 수 없음")).toBeInTheDocument();
    expect(screen.getByText("워커")).toBeInTheDocument();
    expect(screen.getByText("alice → bob")).toBeInTheDocument();
    // 세션 경로는 칩을 붙이지 않는다 — viaLabel이 null을 준다.
    expect(screen.queryByText("session")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc%2F1/activity");
  });

  it("더 보기는 다음 쪽을 이어 붙인다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items: [updated, ownerChanged], next_before_id: 11 }))
      .mockResolvedValueOnce(jsonResponse({ items: [created], next_before_id: null }));
    vi.stubGlobal("fetch", fetchMock);

    render(<DocumentActivity documentId="doc-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "더 보기" }));

    expect(await screen.findByText("carol")).toBeInTheDocument();
    expect(screen.getByText("텍스트 수정(v2)")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/doc-1/activity?before_id=11");
    await waitFor(() => expect(screen.queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument());
  });

  it("기록이 없으면 빈 상태를 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ items: [], next_before_id: null })));

    render(<DocumentActivity documentId="doc-1" />);

    expect(await screen.findByText("아직 기록이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("서버 오류 문구를 그대로 보여 준다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "문서를 찾을 수 없습니다." }, 404)));

    render(<DocumentActivity documentId="doc-1" />);

    expect(await screen.findByRole("status")).toHaveTextContent("문서를 찾을 수 없습니다.");
  });

  it("서버 오류가 아니면 기본 문구를 보여 준다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));

    render(<DocumentActivity documentId="doc-1" />);

    expect(await screen.findByRole("status")).toHaveTextContent("활동 기록을 불러오지 못했습니다.");
  });

  it("사라지면 진행 중인 요청을 취소한다", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init?: RequestInit) => {
        signal = init?.signal ?? undefined;
        return new Promise<Response>(() => {});
      }),
    );

    const { unmount } = render(<DocumentActivity documentId="doc-1" />);
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal?.aborted).toBe(false);

    unmount();

    expect(signal?.aborted).toBe(true);
  });
});
