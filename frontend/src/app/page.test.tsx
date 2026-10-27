// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import Home from "./page";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ auth: { authenticated: true, username: "사용자" } }) }));
vi.mock("@/lib/api", () => ({ countDocuments: vi.fn(), getDocumentProgress: vi.fn(), listDocuments: vi.fn(), listUnfiled: vi.fn(), getDocument: vi.fn() }));
vi.mock("@/lib/useFolders", () => ({ useFolders: () => ({ loading: false, error: null, folders: [
  { id: "root", name: "업무", parent_id: null, department: { name: "부서" }, document_count: 2 },
  { id: "child", name: "하위", parent_id: "root", document_count: 3 },
] }) }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.countDocuments).mockResolvedValue(12);
  vi.mocked(api.getDocumentProgress).mockResolvedValue({ ready: 7, pending: 1, processing: 1, extracting: 1, extraction_failed: 1, error: 1 });
  vi.mocked(api.listDocuments).mockResolvedValue([{ id: "doc", title: "최근 자료", content_type: "pdf", owner_id: "작성자", folder_path: null, updated_at: "2026-10-27T22:12:00+09:00", embedding_status: "ready", extraction_status: "done" } as never]);
  vi.mocked(api.listUnfiled).mockResolvedValue({ count: 0, items: [] });
});
it("인사·통계·최근 수정 문서와 보이는 폴더의 하위 합계를 표시한다", async () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "안녕하세요, 사용자님" })).toBeInTheDocument();
  expect(await screen.findByText("7 / 12")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(screen.getByText("5건")).toBeInTheDocument();
  expect(screen.getByText("처리 대기 3 · 처리 실패 2")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "문서 올리기" })).toHaveAttribute("href", "/documents?upload=1");
  expect(api.listDocuments).toHaveBeenCalledWith({ sort: "updated", limit: 4 }, expect.any(AbortSignal));
  const recent = screen.getByRole("region", { name: "최근 문서" });
  expect(within(recent).getByRole("link", { name: /최근 자료/ })).toHaveAttribute("href", "/documents/doc");
  expect(within(recent).queryByText("폴더 미지정")).not.toBeInTheDocument();
});
it("질문 제출은 검색 주소로 이동하며 빈 질문은 보내지 않는다", () => {
  render(<Home />);
  fireEvent.click(screen.getByRole("button", { name: "검색" }));
  expect(push).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "AI 문서 검색" }), { target: { value: " 대응 절차 " } });
  fireEvent.click(screen.getByRole("button", { name: "검색" }));
  expect(push).toHaveBeenCalledWith("/search?q=%EB%8C%80%EC%9D%91+%EC%A0%88%EC%B0%A8");
});
it("한 조회가 실패해도 다른 구역은 표시한다", async () => {
  vi.mocked(api.countDocuments).mockRejectedValue(new Error("문서 수 오류"));
  render(<Home />);
  expect(await screen.findByText("문서 수 오류")).toBeInTheDocument();
  expect(await screen.findByText("최근 자료")).toBeInTheDocument();
});
it("로딩 숫자는 막대로 표시한다", () => {
  vi.mocked(api.countDocuments).mockReturnValue(new Promise(() => {}));
  render(<Home />);
  expect(screen.getAllByLabelText("불러오는 중").length).toBeGreaterThan(0);
  expect(screen.queryByText("—")).not.toBeInTheDocument();
});
it("정리 대상의 형식·소유자와 폴더 지정 링크를 표시한다", async () => {
  vi.mocked(api.listUnfiled).mockResolvedValue({ count: 1, items: [{ document_id: "new", title: "정리 대상", suggestion: null }] });
  vi.mocked(api.getDocument).mockResolvedValue({ content_type: "md", owner_id: "소유자" } as never);
  render(<Home />);
  expect(await screen.findByText("정리 대상")).toBeInTheDocument();
  expect(await screen.findByText("소유자")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "폴더 지정" })).toHaveAttribute("href", "/organize");
});
