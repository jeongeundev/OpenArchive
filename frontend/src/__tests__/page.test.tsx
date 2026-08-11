import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "@/app/page";
import type { DocumentSummary } from "@/lib/types";

const documents: DocumentSummary[] = [
  {
    id: "document-1",
    title: "OpenSQL 운영 가이드",
    filename: "guide.md",
    content_type: "md",
    version: 1,
    owner_id: "alice",
    visibility: "public",
    tags: ["OpenSQL"],
    embedding_status: "ready",
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T11:00:00Z",
  },
  {
    id: "document-2",
    title: "정합성 점검표",
    filename: "checklist.txt",
    content_type: "txt",
    version: 1,
    owner_id: "alice",
    visibility: "private",
    tags: ["정합성"],
    embedding_status: "pending",
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T11:00:00Z",
  },
];

describe("루트 페이지", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("조회한 문서 목록을 상세 링크로 표시한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(documents), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<Home />);

    expect(screen.getByRole("heading", { name: "문서" })).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "OpenSQL 운영 가이드" }),
    ).toHaveAttribute("href", "/documents/document-1");
    expect(screen.getByRole("link", { name: "정합성 점검표" })).toHaveAttribute(
      "href",
      "/documents/document-2",
    );
    expect(screen.queryByRole("button", { name: "업로드" })).not.toBeInTheDocument();
  });
});
