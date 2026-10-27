// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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
  effective_visibility: "public",
  follows_folder: false,
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

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function detailOf(term: string): HTMLElement | null {
  return screen.getByText(term, { selector: "dt" }).nextElementSibling as HTMLElement | null;
}

describe("DocumentMeta", () => {
  it("「문서 정보」 상자에 파일명·생성 일시·수정 일시를 한 열로 보인다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.getByRole("heading", { level: 2, name: "문서 정보" })).toBeInTheDocument();
    expect(Array.from(window.document.querySelectorAll("dt")).map((node) => node.textContent)).toEqual([
      "파일명",
      "생성 일시",
      "수정 일시",
    ]);
    expect(detailOf("파일명")).toHaveTextContent("guide.md");
    expect(detailOf("생성 일시")).toHaveTextContent(formatDate(document.created_at));
    expect(detailOf("수정 일시")).toHaveTextContent(formatDate(document.updated_at));
  });

  it("제목·배지·형식·소유자·열람 범위는 머리글이 맡으므로 싣지 않는다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(screen.queryByText("완료")).not.toBeInTheDocument();
    expect(screen.queryByText("alice")).not.toBeInTheDocument();
    expect(screen.queryByText("조직 공개")).not.toBeInTheDocument();
    expect(screen.queryByText("유형", { selector: "dt" })).not.toBeInTheDocument();
  });

  it("원본 파일이 없는 문서는 파일명을 「—」로 보인다", () => {
    render(<DocumentMeta document={{ ...document, filename: null }} />);

    expect(detailOf("파일명")).toHaveTextContent("—");
  });

  it("현재 버전으로 색인된 청크 수를 표시한다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.getByText("청크 4개 · 현재 버전(v3) 기준")).toBeInTheDocument();
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
      "text-muted",
    );
  });

  it("편집기와 중복되지 않도록 태그를 표시하지 않는다", () => {
    render(<DocumentMeta document={document} />);

    expect(screen.queryByText("OpenSQL")).not.toBeInTheDocument();
    expect(screen.queryByText("운영")).not.toBeInTheDocument();
  });
});

