// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentDetail } from "@/lib/types";
import { DocumentHeader } from "./DocumentHeader";

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

function metaLine(): HTMLElement {
  return screen.getByTestId("document-header-meta");
}

describe("DocumentHeader", () => {
  it("제목을 큰 머리글로, 임베딩 상태 배지를 옆에 보인다", () => {
    render(<DocumentHeader document={document} />);

    expect(screen.getByRole("heading", { level: 1, name: "OpenSQL 운영 가이드" })).toHaveClass("text-3xl");
    expect(screen.getByText("완료")).toBeInTheDocument();
  });

  it("메타 한 줄을 위치 · 형식 · 소유자 · 열람 범위 · 수정 일시 순으로 보인다", () => {
    render(
      <DocumentHeader
        document={{
          ...document,
          content_type: "hwpx",
          owner_id: "IT전략실_김민준",
          effective_visibility: "private",
          follows_folder: true,
          folder: {
            id: "folder-2",
            name: "통합유지보수",
            path: [
              { id: "folder-1", name: "IT전략실" },
              { id: "folder-2", name: "통합유지보수" },
            ],
          },
        }}
      />,
    );

    expect(metaLine()).toHaveTextContent(
      `IT전략실 › 통합유지보수 · HWPX · IT전략실_김민준 · 제한 · 폴더 범위 따름 · ${formatDate(document.updated_at)}`,
    );
    expect(metaLine()).toHaveClass("text-sm", "text-muted");
  });

  it("소유자에 부서가 있으면 `이름(부서)`로 보인다 (D16)", () => {
    render(<DocumentHeader document={{ ...document, owner_id: "김민준", owner_department: { name: "IT전략실", folder_id: "f1" } }} />);

    expect(metaLine()).toHaveTextContent(`MD · 김민준(IT전략실) · 조직 공개 · ${formatDate(document.updated_at)}`);
    expect(screen.getByRole("link", { name: "IT전략실" })).toHaveAttribute("href", "/?folder=f1");
  });

  it("폴더 밖 문서는 위치 칸을 통째로 뺀다", () => {
    render(<DocumentHeader document={document} />);

    expect(metaLine().textContent).toBe(`MD · alice · 조직 공개 · ${formatDate(document.updated_at)}`);
  });

  it("볼 수 없는 폴더 안 문서는 폴더 이름도 자물쇠도 보이지 않는다", () => {
    render(<DocumentHeader document={{ ...document, hidden_folder: true }} />);

    expect(metaLine().textContent).toBe(`MD · alice · 조직 공개 · ${formatDate(document.updated_at)}`);
    expect(metaLine().textContent).not.toContain("🔒");
    expect(screen.queryByText("볼 수 없는 폴더")).not.toBeInTheDocument();
  });

  it("열람 범위를 조직 공개·제한으로 표시한다", () => {
    const { rerender } = render(<DocumentHeader document={document} />);
    expect(metaLine()).toHaveTextContent("조직 공개");

    rerender(<DocumentHeader document={{ ...document, visibility: "private", effective_visibility: "private" }} />);
    expect(metaLine()).toHaveTextContent("제한");
    expect(metaLine()).not.toHaveTextContent("조직 공개");
  });

  it("폴더 범위를 따르는 문서는 자기 범위가 아니라 실제로 적용되는 범위를 표시한다", () => {
    // 폴더로 만든 문서의 자기 범위는 「제한」으로 닫혀 있다 — 조직 공개 폴더 안이면 조직 전체가 본다.
    render(<DocumentHeader document={{ ...document, visibility: "private", effective_visibility: "public" }} />);
    expect(metaLine()).toHaveTextContent("조직 공개");
    expect(metaLine()).not.toHaveTextContent("제한");
  });

  it("폴더 범위를 따르는 문서는 범위 뒤에 「폴더 범위 따름」을 붙인다 (D3 — 부여 대상 이름은 없다)", () => {
    const { rerender } = render(
      <DocumentHeader document={{ ...document, effective_visibility: "private", follows_folder: true }} />,
    );
    expect(metaLine()).toHaveTextContent("제한 · 폴더 범위 따름");

    rerender(<DocumentHeader document={{ ...document, effective_visibility: "public", follows_folder: true }} />);
    expect(metaLine()).toHaveTextContent("조직 공개 · 폴더 범위 따름");
  });

  it("텍스트 인식 중이면 임베딩 상태 대신 인식 중 배지를 보인다", () => {
    render(<DocumentHeader document={{ ...document, extraction_status: "pending", embedding_status: "pending" }} />);

    expect(screen.getByText("텍스트 인식 중")).toBeInTheDocument();
    expect(screen.queryByText("대기 중")).not.toBeInTheDocument();
  });

  it("텍스트 인식에 실패하면 실패 배지를 보인다", () => {
    render(<DocumentHeader document={{ ...document, extraction_status: "failed", embedding_status: "ready" }} />);

    expect(screen.getByText("텍스트 인식 실패")).toBeInTheDocument();
    expect(screen.queryByText("완료")).not.toBeInTheDocument();
  });

  it("편집기와 중복되지 않도록 태그를 표시하지 않는다", () => {
    render(<DocumentHeader document={document} />);

    expect(screen.queryByText("OpenSQL")).not.toBeInTheDocument();
    expect(screen.queryByText("운영")).not.toBeInTheDocument();
  });
});
