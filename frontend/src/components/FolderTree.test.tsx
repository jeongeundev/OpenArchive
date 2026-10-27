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
    expect(screen.getByRole("button", { name: /인사/ })).toHaveTextContent("3");
  });

  it("부서 폴더에는 「부서 폴더 · 부서」를 작게 표시하고 다른 폴더에는 없다 (D15)", () => {
    render(<FolderTree folders={[{ ...folder("a", "IT전략실 문서", null, 2), department: { id: "g1", name: "IT전략실" } }, folder("b", "인사", null)]}
      selectedId={null} onSelect={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: /IT전략실 문서/ })).toHaveTextContent("부서 폴더 · IT전략실");
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

  it("「전체 문서」 바로 아래 「부여받은 문서」를 두고, 누르면 그 보기를 알린다 (D20)", () => {
    const onSelect = vi.fn();
    const onSelectGranted = vi.fn();
    render(<FolderTree folders={[folder("a", "인사", null, 2)]} selectedId={null} granted={false}
      onSelect={onSelect} onSelectGranted={onSelectGranted} onCreate={vi.fn()} />);
    const all = screen.getByRole("button", { name: "전체 문서" });
    const granted = screen.getByRole("button", { name: "부여받은 문서" });
    expect(all.nextElementSibling).toBe(granted);
    expect(granted).toHaveTextContent(/^부여받은 문서$/);
    fireEvent.click(granted);
    expect(onSelectGranted).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /인사/ }));
    expect(onSelect).toHaveBeenLastCalledWith("a");
  });

  it("「부여받은 문서」를 보는 중이면 그 항목만 현재 위치다", () => {
    render(<FolderTree folders={[folder("a", "인사", null)]} selectedId={null} granted
      onSelect={vi.fn()} onSelectGranted={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByRole("button", { name: "부여받은 문서" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "전체 문서" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: /인사/ })).not.toHaveAttribute("aria-current");
  });
});
