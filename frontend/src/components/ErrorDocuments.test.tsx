import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "@/lib/types";
import { setCurrentUser } from "@/lib/user";
import { ErrorDocuments } from "./ErrorDocuments";

const document: DocumentSummary = {
  id: "doc-1", title: "실패 문서", filename: "failed.md", content_type: "md", version: 1,
  owner_id: "alice", visibility: "public", tags: [], embedding_status: "error",
  created_at: "2026-08-05T10:00:00Z", updated_at: "2026-08-05T11:00:00Z",
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("ErrorDocuments", () => {
  beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); setCurrentUser("alice"); });

  it("실패 문서를 표시하고 재임베딩 후 목록을 갱신한다", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response([document])).mockResolvedValueOnce(response(document)).mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<ErrorDocuments />);
    expect(await screen.findByText("실패 문서")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "재임베딩" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/documents/doc-1/reembed", expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });

  it("재임베딩 403 응답의 detail을 표시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response([document])).mockResolvedValueOnce(response({ detail: "소유자만 요청할 수 있습니다." }, 403)));
    render(<ErrorDocuments />);
    fireEvent.click(await screen.findByRole("button", { name: "재임베딩" }));
    expect(await screen.findByText("소유자만 요청할 수 있습니다.")).toBeInTheDocument();
  });

  it("익명이면 재임베딩을 비활성화한다", async () => {
    setCurrentUser(null);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([document])));
    render(<ErrorDocuments />);
    expect(await screen.findByRole("button", { name: "재임베딩" })).toBeDisabled();
    expect(screen.getByText("사용자를 선택하면 재임베딩을 요청할 수 있습니다.")).toBeInTheDocument();
  });

  it("실패 문서가 없으면 빈 목록 문구를 표시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([])));
    render(<ErrorDocuments />);
    expect(await screen.findByText("실패한 문서가 없습니다.")).toBeInTheDocument();
  });
});
