import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentSummary } from "@/lib/types";
import { DocumentTable } from "./DocumentTable";

const documents: DocumentSummary[] = [
  {
    id: "document-1",
    title: "OpenSQL 운영 가이드",
    filename: "guide.md",
    content_type: "md",
    version: 1,
    owner_id: "alice",
    visibility: "public",
    tags: ["OpenSQL", "운영"],
    embedding_status: "ready",
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T11:00:00Z",
  },
  {
    id: "document-2",
    title: "비공개 점검표",
    filename: null,
    content_type: "txt",
    version: 2,
    owner_id: "alice",
    visibility: "private",
    tags: [],
    embedding_status: "processing",
    created_at: "2026-08-05T09:00:00Z",
    updated_at: "2026-08-05T12:00:00Z",
  },
];

describe("DocumentTable", () => {
  it("각 문서 제목을 상세 화면 링크로 표시한다", () => {
    render(<DocumentTable documents={documents} />);

    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/document-1",
    );
    expect(screen.getByText("공개")).toBeInTheDocument();
    expect(screen.getByText("비공개")).toBeInTheDocument();
  });

  it("문서마다 상태 배지를 하나씩 표시한다", () => {
    render(<DocumentTable documents={documents} />);

    expect(screen.getByText("완료")).toBeInTheDocument();
    expect(screen.getByText("처리 중…")).toBeInTheDocument();
  });

  it("빈 목록에는 안내 문구를 표시한다", () => {
    render(<DocumentTable documents={[]} />);

    expect(screen.getByText("아직 문서가 없습니다.")).toHaveClass("text-neutral-500");
  });
});
