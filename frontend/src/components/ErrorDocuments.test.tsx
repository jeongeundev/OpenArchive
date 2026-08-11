import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import type { DocumentSummary } from "@/lib/types";
import { ErrorDocuments } from "./ErrorDocuments";

const document: DocumentSummary = {
  id: "doc-1", title: "실패 문서", filename: "failed.md", content_type: "md", version: 1,
  owner_id: "alice", visibility: "public", tags: [], embedding_status: "error",
  created_at: "2026-08-05T10:00:00Z", updated_at: "2026-08-05T11:00:00Z",
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function renderDocuments(fetchMock: ReturnType<typeof vi.fn>): void {
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthProvider><ErrorDocuments /></AuthProvider>);
}

describe("ErrorDocuments", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("실패 문서를 표시하고 재임베딩 후 목록을 갱신한다", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/me") return Promise.resolve(response({ authenticated: true, username: "alice", is_admin: false }));
      if (url.endsWith("/reembed")) return Promise.resolve(response(document));
      return Promise.resolve(response([document]));
    });
    renderDocuments(fetchMock);
    expect(await screen.findByText("실패 문서")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "재임베딩" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/documents/doc-1/reembed", expect.objectContaining({ method: "POST" })));
  });

  it("익명에게 재임베딩 작업을 노출하지 않는다", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(response(
      url === "/api/auth/me"
        ? { authenticated: false, username: null, is_admin: false }
        : [document],
    )));
    renderDocuments(fetchMock);
    expect(await screen.findByText("실패 문서")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "재임베딩" })).not.toBeInTheDocument();
  });

  it("실패 문서가 없으면 빈 목록 문구를 표시한다", async () => {
    renderDocuments(vi.fn((url: string) => Promise.resolve(response(
      url === "/api/auth/me"
        ? { authenticated: true, username: "alice", is_admin: false }
        : [],
    ))));
    expect(await screen.findByText("실패한 문서가 없습니다.")).toBeInTheDocument();
  });
});
