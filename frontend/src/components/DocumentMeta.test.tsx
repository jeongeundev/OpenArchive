import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentDetail } from "@/lib/types";
import { DocumentMeta } from "./DocumentMeta";

const document: DocumentDetail = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.md",
  content_type: "md",
  content: "추출된 텍스트",
  version: 3,
  owner_id: "alice",
  visibility: "public",
  tags: ["OpenSQL", "운영"],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  chunk_count: 4,
  chunk_version: 3,
};

describe("DocumentMeta", () => {
  it("현재 버전으로 색인된 청크 수를 표시한다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.getByText("청크 4개 · 현재 버전(v3) 기준")).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
  });

  it("이전 버전 청크로 검색 중인 상태를 오류 없이 표시한다", () => {
    render(
      <DocumentMeta
        document={{ ...document, embedding_status: "processing", chunk_version: 2 }}
      />,
    );

    expect(screen.getByText("청크 4개 · v2 기준 — 재임베딩 중입니다")).toBeInTheDocument();
  });

  it("청크가 없으면 미색인 안내를 표시한다", () => {
    render(<DocumentMeta document={{ ...document, chunk_count: 0, chunk_version: null }} />);

    expect(screen.getByText("아직 색인된 청크가 없습니다.")).toHaveClass(
      "text-neutral-500",
    );
  });

  it("편집기와 중복되지 않도록 태그를 표시하지 않는다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.queryByText("OpenSQL")).not.toBeInTheDocument();
    expect(screen.queryByText("운영")).not.toBeInTheDocument();
  });
});
