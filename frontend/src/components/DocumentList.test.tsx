// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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

function renderList(items: DocumentSummary[]) { return render(<DocumentList documents={items} />); }

describe("DocumentList", () => {
  it("표의 여섯 열과 상세 링크, 태그, 소유자, 범위, 날짜, 상태를 표시한다", () => {
    renderList([{ ...documents[0], folder_path: ["인사", "규정"], owner_department: { name: "총무", folder_id: "f1" }, follows_folder: true }, documents[1]]);
    expect(screen.getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["이름", "위치", "소유자", "열람 범위", "수정일", "상태"]);
    expect(screen.getByRole("link", { name: documents[0].title })).toHaveAttribute("href", "/documents/document-1");
    for (const text of ["#OpenSQL", "#운영", "인사 › 규정", "alice", "조직 공개", "제한", "완료", "처리 중…"]) expect(screen.getAllByText(text).length).toBeGreaterThan(0);
    // 「폴더 범위 따름」은 보조 줄 대신 열람 범위 알약의 title로 남는다.
    expect(screen.queryByText("폴더 범위 따름")).not.toBeInTheDocument();
    expect(screen.getByText("조직 공개")).toHaveAttribute("title", "폴더 범위 따름");
    expect(screen.getByText("제한")).not.toHaveAttribute("title");
    expect(screen.getByText("완료")).toHaveClass("text-done");
    expect(screen.getByText("완료")).not.toHaveClass("rounded-full");
    expect(screen.getByRole("link", { name: "총무" })).toHaveAttribute("href", "/documents?folder=f1");
    expect(screen.getAllByRole("cell")[4]).toHaveTextContent(/2026\.08\.05 \d{2}:\d{2}/);
    expect(screen.queryByRole("button", { name: "정보" })).not.toBeInTheDocument();
  });
  it("폴더가 없거나 볼 수 없는 폴더면 위치는 「폴더 미지정」이고 태그 없는 문서는 태그를 만들지 않는다", () => {
    renderList([{ ...documents[1], folder_path: null }]);
    expect(screen.getAllByRole("cell")[1]).toHaveTextContent(/^폴더 미지정$/);
    expect(screen.getAllByRole("cell")[1]).toHaveClass("text-muted");
    expect(screen.queryByText(/🔒/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^#/)).not.toBeInTheDocument();
  });
  it.each([
    ["pdf", "bg-failed-soft", "text-failed"],
    ["xlsx", "bg-done-soft", "text-done"],
    ["hwpx", "bg-accent-soft", "text-accent"],
    ["docx", "bg-accent-soft", "text-accent"],
  ] as const)("유형 배지는 유형별 의미 토큰 색이다 — %s", (type, bg, text) => {
    renderList([{ ...documents[0], content_type: type }]);
    expect(screen.getByText(type)).toHaveClass(bg, text, "font-bold", "uppercase");
  });
  it("수정일은 한 줄이고 행은 위쪽 정렬이다", () => {
    renderList([documents[0]]);
    expect(screen.getAllByRole("cell")[4]).toHaveClass("whitespace-nowrap");
    for (const cell of screen.getAllByRole("cell")) expect(cell).toHaveClass("align-top");
  });
  it("실효 범위를 사용하고 인식 상태를 우선한다", () => {
    renderList([{ ...documents[1], effective_visibility: "public", extraction_status: "pending" }]);
    expect(screen.getByText("조직 공개")).toBeInTheDocument();
    expect(screen.queryByText("제한")).not.toBeInTheDocument();
    expect(screen.getByText("텍스트 인식 중")).toBeInTheDocument();
    expect(screen.queryByText("처리 중…")).not.toBeInTheDocument();
  });
  it("좁은 화면은 행을 쌓고 넓은 화면만 표로 배치한다", () => {
    renderList(documents);
    expect(screen.getByRole("table")).toHaveClass("block", "lg:table");
    expect(screen.getAllByRole("row")[1]).toHaveClass("block", "lg:table-row");
  });

  it("볼 수 없는 부서 폴더는 이름만 표시하고 링크를 만들지 않는다", () => {
    renderList([{ ...documents[0], owner_department: { name: "총무", folder_id: null } }]);
    expect(screen.getByText("총무")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "총무" })).not.toBeInTheDocument();
  });
  it("텍스트 인식 실패 배지도 임베딩 대기보다 우선한다", () => {
    renderList([{ ...documents[0], extraction_status: "failed", embedding_status: "pending" }]);
    expect(screen.getByText("텍스트 인식 실패")).toBeInTheDocument();
    expect(screen.queryByText("대기 중")).not.toBeInTheDocument();
  });
  it("형식 칩은 제목 앞에 있고 제목은 긴 텍스트를 줄바꿈한다", () => {
    renderList([{ ...documents[0], content_type: "hwpx" }]);
    const label = screen.getByText("hwpx");
    const title = screen.getByRole("link", { name: documents[0].title });
    expect(label).toHaveClass("uppercase", "whitespace-nowrap");
    expect(label.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(title).toHaveClass("break-words");
    expect(title).not.toHaveClass("whitespace-nowrap");
  });
  it("빈 목록 안내", () => { renderList([]); expect(screen.getByText("아직 문서가 없습니다.")).toHaveClass("text-muted"); });
});
