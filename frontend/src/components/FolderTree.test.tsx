// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { Folder } from "@/lib/types";
import { FolderTree } from "./FolderTree";

const scope = { visibility: "public" as const, users: [], groups: [] };
function folder(id: string, name: string, parentId: string | null, documentCount = 0): Folder {
  return { id, name, parent_id: parentId, created_by: "kim", document_count: documentCount, scope,
    inherited: parentId !== null, can_manage: true, can_change_access: parentId === null, can_share: false };
}

describe("FolderTree", () => {
  it("「새 폴더」로 이름을 입력하면 최상위 폴더 생성을 요청한다", async () => {
    const onCreate = vi.fn(() => Promise.resolve());
    render(<FolderTree folders={[]} selectedId={null} onSelect={vi.fn()} onCreate={onCreate} />);
    fireEvent.click(screen.getByRole("button", { name: "새 폴더" }));
    fireEvent.change(screen.getByLabelText("새 폴더 이름"), { target: { value: "인사" } });
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("인사", null));
    await waitFor(() => expect(screen.queryByLabelText("새 폴더 이름")).not.toBeInTheDocument());
  });

  it("만들기에 실패하면 서버 문구를 보이고 입력을 남긴다", async () => {
    const onCreate = vi.fn(() => Promise.reject(new ApiError(409, "같은 이름의 폴더가 있습니다.")));
    render(<FolderTree folders={[]} selectedId={null} onSelect={vi.fn()} onCreate={onCreate} />);
    fireEvent.click(screen.getByRole("button", { name: "새 폴더" }));
    fireEvent.change(screen.getByLabelText("새 폴더 이름"), { target: { value: "인사" } });
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("같은 이름의 폴더가 있습니다.");
    expect(screen.getByLabelText("새 폴더 이름")).toHaveValue("인사");
  });

  it("하위 폴더를 상위 폴더 아래 계층으로, 문서 수와 함께 표시한다", () => {
    render(<FolderTree folders={[folder("b", "채용", "a", 1), folder("a", "인사", null, 3)]}
      selectedId={null} onSelect={vi.fn()} onCreate={vi.fn()} />);
    const parent = screen.getByRole("button", { name: /인사/ }).closest("li");
    expect(parent).not.toBeNull();
    const child = within(parent as HTMLElement).getByRole("button", { name: /채용/ });
    expect(child).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /인사/ })).toHaveTextContent("4");
  });

  it("부서 폴더에는 「부서 폴더 · 부서」를 작게 표시하고 다른 폴더에는 없다 (D15)", () => {
    render(<FolderTree folders={[{ ...folder("a", "IT전략실 문서", null, 2), department: { id: "g1", name: "IT전략실" } }, folder("b", "인사", null)]}
      selectedId={null} onSelect={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /IT전략실 문서/ })).not.toHaveTextContent("부서 폴더");
    expect(screen.getByRole("button", { name: /인사/ })).not.toHaveTextContent("부서 폴더");
  });

  it("폴더를 누르면 그 폴더를, 「전체 문서」를 누르면 선택 해제를 알린다", () => {
    const onSelect = vi.fn();
    render(<FolderTree folders={[folder("a", "인사", null)]} selectedId="a" onSelect={onSelect} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /인사/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "전체 문서" })).not.toHaveAttribute("aria-current");
    fireEvent.click(screen.getByRole("button", { name: /인사/ }));
    expect(onSelect).toHaveBeenLastCalledWith("a");
    fireEvent.click(screen.getByRole("button", { name: "전체 문서" }));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("부여받은 문서와 휴지통은 트리에 없고 전체 건수를 표시한다", () => {
    render(<FolderTree folders={[]} selectedId={null} granted total={12} onSelect={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /전체 문서/ })).toHaveTextContent("12");
    expect(screen.queryByText("부여받은 문서")).not.toBeInTheDocument();
    expect(screen.queryByText("휴지통")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /전체 문서/ })).not.toHaveAttribute("aria-current");
  });

  it("맨 아래 「폴더 미지정」 줄은 수와 함께 보이고 누르면 그 보기를 알린다", () => {
    const onSelectUnfiled = vi.fn();
    render(<FolderTree folders={[folder("a", "인사", null)]} selectedId={null} unfiledCount={3}
      onSelect={vi.fn()} onSelectUnfiled={onSelectUnfiled} onCreate={vi.fn()} />);
    const row = screen.getByRole("button", { name: /폴더 미지정/ });
    expect(row).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: /인사/ }).compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(row);
    expect(onSelectUnfiled).toHaveBeenCalledOnce();
  });

  it("폴더 미지정 문서가 없으면 그 줄을 숨긴다", () => {
    render(<FolderTree folders={[]} selectedId={null} unfiledCount={0} onSelect={vi.fn()} onSelectUnfiled={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /폴더 미지정/ })).not.toBeInTheDocument();
  });

  it("「폴더 미지정」 보기에서는 그 줄이 선택되고 「전체 문서」는 선택이 아니다", () => {
    render(<FolderTree folders={[]} selectedId={null} unfiledCount={2} unfiledSelected onSelect={vi.fn()} onSelectUnfiled={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /폴더 미지정/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /폴더 미지정/ })).toHaveClass("bg-accent-soft", "text-accent", "font-semibold");
    expect(screen.getByRole("button", { name: /전체 문서/ })).not.toHaveAttribute("aria-current");
  });

  it("선택 줄은 강조색 칸이고 각 폴더 줄 앞에 폴더 아이콘이 있다 — 깊이는 들여쓰기만", () => {
    const { container } = render(<FolderTree folders={[folder("a", "인사", null), folder("b", "채용", "a")]} selectedId="b"
      onSelect={vi.fn()} onCreate={vi.fn()} />);
    const selected = screen.getByRole("button", { name: /채용/ });
    expect(selected).toHaveClass("bg-accent-soft", "text-accent", "font-semibold");
    expect(screen.getByRole("button", { name: /인사/ })).not.toHaveClass("bg-accent-soft");
    for (const name of [/인사/, /채용/]) expect(screen.getByRole("button", { name }).querySelector("svg[aria-hidden='true']")).not.toBeNull();
    expect(container.querySelector("ul ul")).not.toHaveClass("border-l");
  });

  it("「전체 문서」가 선택이면 같은 강조색 칸이다", () => {
    render(<FolderTree folders={[]} selectedId={null} onSelect={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /전체 문서/ })).toHaveClass("bg-accent-soft", "text-accent", "font-semibold");
  });
});
