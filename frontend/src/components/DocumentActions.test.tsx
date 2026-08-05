import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "@/lib/types";
import { setCurrentUser } from "@/lib/user";
import { DocumentActions } from "./DocumentActions";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const document: DocumentSummary = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  version: 2,
  owner_id: "alice",
  visibility: "public",
  tags: [],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
};

describe("DocumentActions", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    push.mockReset();
    setCurrentUser("alice");
  });

  it("확인 후 문서와 청크·벡터 삭제를 요청하고 목록으로 이동한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<DocumentActions disabled={false} document={document} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect(window.confirm).toHaveBeenCalledWith(
      "문서를 삭제하시겠습니까? 청크와 벡터도 함께 삭제됩니다.",
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
    expect(push).toHaveBeenCalledWith("/");
  });

  it("임베딩 오류 상태에서만 재임베딩을 제공한다", () => {
    const { rerender } = render(
      <DocumentActions disabled={false} document={document} onChanged={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "재임베딩" })).not.toBeInTheDocument();

    rerender(
      <DocumentActions
        disabled={false}
        document={{ ...document, embedding_status: "error" }}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "재임베딩" })).toBeInTheDocument();
  });

  it("비활성 상태에서는 삭제와 재임베딩을 모두 막는다", () => {
    render(
      <DocumentActions
        disabled
        document={{ ...document, embedding_status: "error" }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "삭제" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "재임베딩" })).toBeDisabled();
  });
});
