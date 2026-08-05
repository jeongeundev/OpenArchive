import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, updateTags } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";
import { TagEditor } from "./TagEditor";

vi.mock("@/lib/api", () => ({
  updateTags: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;

    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

const document: DocumentDetail = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  content: "추출된 텍스트",
  version: 2,
  owner_id: "alice",
  visibility: "public",
  tags: ["OpenSQL"],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  chunk_count: 1,
  chunk_version: 2,
};

describe("TagEditor", () => {
  beforeEach(() => {
    vi.mocked(updateTags).mockReset();
  });

  it("태그를 추가하고 전체 목록을 저장한다", async () => {
    vi.mocked(updateTags).mockResolvedValue(document);
    const onSaved = vi.fn();
    render(<TagEditor disabled={false} document={document} onSaved={onSaved} />);

    fireEvent.change(screen.getByRole("textbox", { name: "태그" }), {
      target: { value: "pgvector" },
    });
    fireEvent.click(screen.getByRole("button", { name: "태그 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "태그 저장" }));

    await waitFor(() => {
      expect(updateTags).toHaveBeenCalledWith("document-1", ["OpenSQL", "pgvector"]);
    });
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it("태그를 삭제하고 빠진 전체 목록을 저장한다", async () => {
    vi.mocked(updateTags).mockResolvedValue({ ...document, tags: [] });
    render(<TagEditor disabled={false} document={document} onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "OpenSQL 태그 삭제" }));
    fireEvent.click(screen.getByRole("button", { name: "태그 저장" }));

    await waitFor(() => expect(updateTags).toHaveBeenCalledWith("document-1", []));
  });

  it("403 저장 실패 시 에러와 입력 내용을 보존한다", async () => {
    vi.mocked(updateTags).mockRejectedValue(new ApiError(403, "태그를 수정할 권한이 없습니다."));
    render(<TagEditor disabled={false} document={document} onSaved={vi.fn()} />);

    const input = screen.getByRole("textbox", { name: "태그" });
    fireEvent.change(input, { target: { value: "보존할 태그" } });
    fireEvent.click(screen.getByRole("button", { name: "태그 저장" }));

    expect(await screen.findByText("태그를 수정할 권한이 없습니다.")).toBeInTheDocument();
    expect(input).toHaveValue("보존할 태그");
  });

  it("비활성 상태에서는 입력과 모든 버튼을 막는다", () => {
    render(<TagEditor disabled document={document} onSaved={vi.fn()} />);

    expect(screen.getByRole("textbox", { name: "태그" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "태그 추가" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "태그 저장" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "OpenSQL 태그 삭제" })).toBeDisabled();
  });
});
