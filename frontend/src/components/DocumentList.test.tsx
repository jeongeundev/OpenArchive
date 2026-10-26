// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "@/lib/types";
import { DocumentList } from "./DocumentList";

const documents: DocumentSummary[] = [
  {
    id: "document-1",
    title: "OpenSQL 운영 가이드",
    filename: "guide.md",
    content_type: "md",
    version: 1,
    owner_id: "alice",
    visibility: "public",
    effective_visibility: "public",
    follows_folder: false,
    tags: ["OpenSQL", "운영"],
    embedding_status: "ready",
    extraction_status: "done",
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
    effective_visibility: "private",
    follows_folder: false,
    tags: [],
    embedding_status: "processing",
    extraction_status: "done",
    created_at: "2026-08-05T09:00:00Z",
    updated_at: "2026-08-05T12:00:00Z",
  },
];

function renderList(items: DocumentSummary[], props: { selectedId?: string | null; onSelect?: (id: string) => void } = {}) {
  return render(<DocumentList documents={items} selectedId={props.selectedId ?? null} onSelect={props.onSelect ?? vi.fn()} />);
}

function metaItems(row: HTMLElement): string[] {
  return within(row).getAllByTestId("document-meta-item").map((item) => item.textContent ?? "");
}

describe("DocumentList", () => {
  it("각 문서 제목을 상세 화면 링크로 표시한다", () => {
    renderList(documents);

    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/document-1",
    );
    expect(screen.getByText("조직 공개")).toBeInTheDocument();
    expect(screen.getByText("제한")).toBeInTheDocument();
  });

  it("문서마다 소유자를 표시한다 (명세서 187)", () => {
    renderList([{ ...documents[0], owner_id: "kim" }]);

    expect(screen.getByText("kim")).toBeInTheDocument();
  });

  it("문서마다 상태 배지를 하나씩 표시한다", () => {
    renderList(documents);

    const [first, second] = screen.getAllByRole("listitem");
    expect(within(first).getByText("완료")).toBeInTheDocument();
    expect(within(second).getByText("처리 중…")).toBeInTheDocument();
    expect(within(first).queryByText("처리 중…")).not.toBeInTheDocument();
  });

  it("빈 목록에는 안내 문구를 표시한다", () => {
    renderList([]);

    expect(screen.getByText("아직 문서가 없습니다.")).toHaveClass("text-muted");
  });

  it("추출 상태 배지가 임베딩 상태 배지보다 우선한다", () => {
    renderList([
      { ...documents[0], extraction_status: "pending", embedding_status: "pending" },
      { ...documents[1], extraction_status: "failed", embedding_status: "pending" },
    ]);

    expect(screen.getByText("텍스트 인식 중")).toBeInTheDocument();
    expect(screen.getByText("텍스트 인식 실패")).toBeInTheDocument();
    expect(screen.queryByText("대기 중")).not.toBeInTheDocument();
  });

  it("폴더 범위를 따르는 문서는 실제로 적용되는 범위를 표시한다", () => {
    renderList([{ ...documents[1], visibility: "private", effective_visibility: "public" }]);
    expect(screen.getByText("조직 공개")).toBeInTheDocument();
    expect(screen.queryByText("제한")).not.toBeInTheDocument();
  });

  it("폴더 범위를 따르는 문서는 범위 뒤에 「폴더 범위 따름」을 붙인다 (D3 — 부여 대상 이름은 없다)", () => {
    renderList([
      { ...documents[0], effective_visibility: "public", follows_folder: true },
      { ...documents[1], effective_visibility: "private", follows_folder: true, owner_id: "bob" },
    ]);
    expect(screen.getByText("조직 공개 · 폴더 범위 따름")).toBeInTheDocument();
    expect(screen.getByText("제한 · 폴더 범위 따름")).toBeInTheDocument();
    expect(screen.queryByText("제한")).not.toBeInTheDocument();
    expect(screen.queryByText("조직 공개")).not.toBeInTheDocument();
  });

  it("메타 항목·상태 배지·형식 라벨은 줄바꿈하지 않고 제목은 줄바꿈을 허용한다 (1920 폭에서 세로 쪼개짐 방지)", () => {
    renderList([{ ...documents[0], folder_path: ["인사", "규정"] }]);

    const row = screen.getByRole("listitem");
    const items = within(row).getAllByTestId("document-meta-item");
    // 위치·소유자·열람 범위·수정일·태그
    expect(items).toHaveLength(5);
    for (const item of items.slice(0, 4)) expect(item).toHaveClass("whitespace-nowrap");
    expect(within(row).getByText("완료").closest("span.rounded-full")).toHaveClass("whitespace-nowrap");
    expect(within(row).getByText("md")).toHaveClass("whitespace-nowrap");
    expect(within(row).getByRole("link", { name: "OpenSQL 운영 가이드" })).not.toHaveClass("whitespace-nowrap");
  });

  it("메타 줄은 위치 · 소유자 · 열람 범위 · 수정일 · 태그 순이고 위치는 폴더 경로를 › 로 잇는다", () => {
    renderList([{ ...documents[0], folder_path: ["인사", "규정"] }]);

    const items = metaItems(screen.getByRole("listitem"));
    expect(items[0]).toBe("인사 › 규정");
    expect(items[1]).toBe("alice");
    expect(items[2]).toBe("조직 공개");
    expect(items[3]).toMatch(/2026/);
    expect(items[4]).toBe("OpenSQL, 운영");
    expect(screen.getByText("인사 › 규정")).toHaveClass("text-body", "font-medium");
  });

  it("폴더 경로가 없거나 null이면 위치 자리가 통째로 빠진다", () => {
    renderList([{ ...documents[0] }, { ...documents[0], id: "document-3", folder_path: null }]);

    for (const row of screen.getAllByRole("listitem")) {
      const items = metaItems(row);
      expect(items).toHaveLength(4);
      expect(items[0]).toBe("alice");
      expect(row).not.toHaveTextContent("—");
    }
  });

  it("태그가 없으면 태그 자리가 빠진다", () => {
    renderList([documents[1]]);

    const row = screen.getByRole("listitem");
    expect(metaItems(row)).toHaveLength(3);
    expect(row).not.toHaveTextContent("—");
  });

  it("형식 라벨이 제목 앞에 있다", () => {
    renderList([{ ...documents[0], content_type: "hwpx" }]);

    const label = screen.getByText("hwpx");
    expect(label).toHaveClass("uppercase");
    const title = screen.getByRole("link", { name: "OpenSQL 운영 가이드" });
    expect(label.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("행 영역이나 「정보」 버튼을 누르면 고르고, 제목 링크를 누르면 고르지 않는다", () => {
    const onSelect = vi.fn();
    renderList(documents, { onSelect });

    const [first, second] = screen.getAllByRole("listitem");
    fireEvent.click(within(first).getByText("alice"));
    expect(onSelect).toHaveBeenLastCalledWith("document-1");
    fireEvent.click(within(second).getByRole("button", { name: "정보" }));
    expect(onSelect).toHaveBeenLastCalledWith("document-2");
    expect(onSelect).toHaveBeenCalledTimes(2);

    fireEvent.click(within(first).getByRole("link", { name: "OpenSQL 운영 가이드" }));
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("고른 행은 aria-current로, 「정보」 버튼은 aria-pressed로 상태를 알린다", () => {
    renderList(documents, { selectedId: "document-2" });

    const [first, second] = screen.getAllByRole("listitem");
    expect(second).toHaveAttribute("aria-current", "true");
    expect(first).not.toHaveAttribute("aria-current");
    expect(within(second).getByRole("button", { name: "정보" })).toHaveAttribute("aria-pressed", "true");
    expect(within(first).getByRole("button", { name: "정보" })).toHaveAttribute("aria-pressed", "false");
  });
});
