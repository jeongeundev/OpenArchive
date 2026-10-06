import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentDetail } from "@/lib/types";
import { DocumentMeta } from "./DocumentMeta";

const document: DocumentDetail = {
  folder: null,
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
  extraction_status: "done",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  files: [],
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

  it("열람 범위를 조직 공개·제한으로 표시한다", () => {
    const { rerender } = render(<DocumentMeta document={document} />);
    expect(screen.getByText("조직 공개")).toBeInTheDocument();

    rerender(<DocumentMeta document={{ ...document, visibility: "private", effective_visibility: "private" }} />);
    expect(screen.getByText("제한")).toBeInTheDocument();
  });

  it("폴더 범위를 따르는 문서는 자기 범위가 아니라 실제로 적용되는 범위를 표시한다", () => {
    // 폴더로 만든 문서의 자기 범위는 「제한」으로 닫혀 있다 — 조직 공개 폴더 안이면 조직 전체가 본다.
    render(<DocumentMeta document={{ ...document, visibility: "private", effective_visibility: "public" }} />);
    expect(screen.getByText("조직 공개")).toBeInTheDocument();
    expect(screen.queryByText("제한")).not.toBeInTheDocument();
  });

  it("편집기와 중복되지 않도록 태그를 표시하지 않는다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.queryByText("OpenSQL")).not.toBeInTheDocument();
    expect(screen.queryByText("운영")).not.toBeInTheDocument();
  });

  it("텍스트 인식 중이면 임베딩 상태 대신 인식 중 배지를 보인다", () => {
    render(<DocumentMeta document={{ ...document, extraction_status: "pending", embedding_status: "pending" }} />);

    expect(screen.getByText("텍스트 인식 중")).toBeInTheDocument();
    expect(screen.queryByText("대기 중")).not.toBeInTheDocument();
  });

  it("텍스트 인식에 실패하면 실패 배지를 보인다", () => {
    render(<DocumentMeta document={{ ...document, extraction_status: "failed", embedding_status: "ready" }} />);

    expect(screen.getByText("텍스트 인식 실패")).toBeInTheDocument();
    expect(screen.queryByText("완료")).not.toBeInTheDocument();
  });
});
