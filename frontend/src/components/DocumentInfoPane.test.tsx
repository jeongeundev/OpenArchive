// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDocument, getRelated, getTagSuggestions } from "@/lib/api";
import type { DocumentDetail, OriginalFile, RelatedResponse } from "@/lib/types";
import { DocumentInfoPane } from "./DocumentInfoPane";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocument: vi.fn(),
  getRelated: vi.fn(),
  getTagSuggestions: vi.fn(),
}));

function file(fileVersion: number, preview: OriginalFile["preview_status"]): OriginalFile {
  return {
    file_version: fileVersion,
    filename: `계획-v${fileVersion}.hwpx`,
    size: 1024,
    sha256: "abc",
    text_version: fileVersion,
    preview_status: preview,
    uploaded_by: "alice",
    uploaded_at: "2026-08-05T10:00:00Z",
  };
}

const detail: DocumentDetail = {
  id: "document-1",
  title: "2026 사업 계획",
  filename: "계획-v2.hwpx",
  content_type: "hwpx",
  version: 3,
  owner_id: "alice",
  visibility: "public",
  effective_visibility: "public",
  follows_folder: false,
  tags: ["계획"],
  embedding_status: "ready",
  extraction_status: "done",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  folder: {
    id: "folder-2",
    name: "기획",
    path: [{ id: "folder-1", name: "경영지원" }, { id: "folder-2", name: "기획" }],
  },
  content: "본문 첫 문단은 창에 나오면 안 된다",
  versions: [],
  files: [file(1, "ready"), file(2, "ready")],
  chunk_count: 2,
  chunk_version: 3,
};

const related: RelatedResponse = {
  items: [
    { document_id: "r-1", title: "관련 하나", tags: [], kind: "overlaps", score: 0.91234, mutual: true },
    { document_id: "r-2", title: "관련 둘", tags: [], kind: "points_to", score: 0.8, mutual: false },
    { document_id: "r-3", title: "관련 셋", tags: [], kind: "refers", score: 0.7, mutual: false },
    { document_id: "r-4", title: "관련 넷", tags: [], kind: "related", score: 0.6, mutual: false },
    { document_id: "r-5", title: "관련 다섯", tags: [], kind: "related", score: 0.5, mutual: false },
  ],
  identical: [],
  based_on_version: 3,
  reason: null,
};

function setup(document: DocumentDetail = detail, response: RelatedResponse = related) {
  vi.mocked(getDocument).mockResolvedValue(document);
  vi.mocked(getRelated).mockResolvedValue(response);
  vi.mocked(getTagSuggestions).mockResolvedValue({ items: [], based_on_version: null, reason: null });
}

describe("DocumentInfoPane", () => {
  beforeEach(() => {
    vi.mocked(getDocument).mockReset();
    vi.mocked(getRelated).mockReset();
    vi.mocked(getTagSuggestions).mockReset();
  });

  it("「문서 정보」 보조 영역에 형식·상태·제목·위치를 표시한다", async () => {
    setup();
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    const pane = screen.getByRole("complementary", { name: "문서 정보" });
    expect(await within(pane).findByText("2026 사업 계획")).toHaveClass("text-lg", "font-semibold");
    expect(within(pane).getByText("hwpx")).toHaveClass("uppercase");
    expect(within(pane).getByText("완료")).toBeInTheDocument();
    expect(within(pane).getAllByText("경영지원 › 기획").length).toBeGreaterThan(0);
  });

  it("불러오는 동안 안내를 표시하고, 실패하면 서버 문구를 표시한다", async () => {
    vi.mocked(getDocument).mockRejectedValue(new Error("문서를 불러오지 못했습니다 (서버)"));
    vi.mocked(getRelated).mockResolvedValue(related);
    vi.mocked(getTagSuggestions).mockResolvedValue({ items: [], based_on_version: null, reason: null });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    expect(await screen.findByText("문서를 불러오지 못했습니다 (서버)")).toBeInTheDocument();
  });

  it("「문서 열기」는 상세 화면 링크다", async () => {
    setup();
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    expect(await screen.findByRole("link", { name: "문서 열기" })).toHaveAttribute("href", "/documents/document-1");
  });

  it("현재 판의 미리보기가 준비됐을 때만 「원본 미리보기」를 새 탭으로 연다", async () => {
    setup();
    const { unmount } = render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    const preview = await screen.findByRole("link", { name: "원본 미리보기" });
    expect(preview).toHaveAttribute("href", "/api/documents/document-1/files/2/preview");
    expect(preview).toHaveAttribute("target", "_blank");
    expect(preview).toHaveAttribute("rel", "noopener noreferrer");
    unmount();

    // 이전 판만 준비돼 있으면 현재 판 미리보기가 아니다.
    setup({ ...detail, files: [file(1, "ready"), file(2, "pending")] });
    const second = render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);
    await screen.findByRole("link", { name: "문서 열기" });
    expect(screen.queryByRole("link", { name: "원본 미리보기" })).not.toBeInTheDocument();
    second.unmount();

    setup({ ...detail, filename: null, files: [] });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);
    await screen.findByRole("link", { name: "문서 열기" });
    expect(screen.queryByRole("link", { name: "원본 미리보기" })).not.toBeInTheDocument();
  });

  it("정보 목록에 위치·소유자·열람 범위·수정·파일을 표시한다", async () => {
    setup({ ...detail, follows_folder: true });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    await screen.findByRole("link", { name: "문서 열기" });
    const terms = screen.getAllByRole("term").map((term) => term.textContent);
    expect(terms).toEqual(["위치", "소유자", "열람 범위", "수정", "파일"]);
    const values = screen.getAllByRole("definition").map((value) => value.textContent ?? "");
    expect(values[0]).toBe("경영지원 › 기획");
    expect(values[1]).toBe("alice");
    expect(values[2]).toBe("조직 공개 · 폴더 범위 따름");
    expect(values[3]).toMatch(/2026.* · 텍스트 v3$/);
    expect(values[4]).toBe("계획-v2.hwpx");
  });

  it("소유자에 부서가 있으면 `이름(부서)`로 보인다 (D16)", async () => {
    setup({ ...detail, owner_id: "김민준", owner_department: { name: "IT전략실", folder_id: "f1" } });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    await screen.findByRole("link", { name: "문서 열기" });
    expect(screen.getAllByRole("definition")[1].textContent).toBe("김민준(IT전략실)");
    expect(screen.getByRole("link", { name: "IT전략실" })).toHaveAttribute("href", "/?folder=f1");
  });

  it("폴더·원본 파일이 없으면 위치·파일 항목이 빠진다", async () => {
    setup({ ...detail, folder: null, filename: null, files: [] });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    await screen.findByRole("link", { name: "문서 열기" });
    expect(screen.getAllByRole("term").map((term) => term.textContent)).toEqual(["소유자", "열람 범위", "수정"]);
  });

  it("관련 문서 상위 4건을 제목 링크·관계 문구·점수로 표시한다", async () => {
    setup();
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    expect(await screen.findByText("관련 문서 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "관련 하나" })).toHaveAttribute("href", "/documents/r-1");
    expect(screen.getByText("여러 대목에서 만난다")).toBeInTheDocument();
    expect(screen.getByText("본문에서 가리킨다")).toBeInTheDocument();
    expect(screen.getByText("0.912")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "관련 넷" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "관련 다섯" })).not.toBeInTheDocument();
  });

  it("관련 문서가 없으면 없다고 알린다", async () => {
    setup(detail, { items: [], identical: [], based_on_version: 3, reason: null });
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    expect(await screen.findByText("관련 문서가 없습니다.")).toBeInTheDocument();
  });

  it("본문 내용을 보이지 않는다 (D10)", async () => {
    setup();
    render(<DocumentInfoPane documentId="document-1" onClose={vi.fn()} />);

    await screen.findByText("관련 문서 4");
    expect(screen.queryByText(/본문 첫 문단/)).not.toBeInTheDocument();
  });

  it("「닫기」를 누르면 onClose를 부른다", async () => {
    setup();
    const onClose = vi.fn();
    render(<DocumentInfoPane documentId="document-1" onClose={onClose} />);

    fireEvent.click(await screen.findByRole("button", { name: "닫기" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
