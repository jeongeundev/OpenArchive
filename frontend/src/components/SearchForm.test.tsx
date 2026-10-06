import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MAX_K } from "@/lib/types";
import type { Folder } from "@/lib/types";
import { SearchForm } from "./SearchForm";

describe("SearchForm", () => {
  it("쉼표로 구분한 태그를 정리해 검색 입력으로 전달한다", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} pending={false} />);

    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "OpenSQL" } });
    fireEvent.change(screen.getByLabelText("태그 (쉼표로 구분)"), {
      target: { value: "운영,  장애, ," },
    });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(onSearch).toHaveBeenCalledWith({
      query: "OpenSQL",
      tags: ["운영", "장애"],
      contentType: null,
      folderId: null,
      k: 10,
    });
  });

  it("유형 전체를 null로 전달하고 결과 수는 MAX_K를 넘지 않는다", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} pending={false} />);

    expect(screen.getByLabelText("문서 유형")).toHaveValue("");
    const kInput = screen.getByLabelText("결과 수");
    expect(kInput).toHaveAttribute("max", String(MAX_K));

    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "벡터" } });
    fireEvent.change(kInput, { target: { value: String(MAX_K + 1) } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(onSearch).toHaveBeenCalledWith(
      expect.objectContaining({ contentType: null, k: MAX_K }),
    );
  });

  const hr: Folder = {
    id: "f-hr", parent_id: null, name: "인사", created_by: "lee", document_count: 1,
    scope: { visibility: "public", users: [], groups: [] },
    inherited: false, can_manage: false, can_change_access: false,
  };
  const hiring: Folder = { ...hr, id: "f-hiring", parent_id: "f-hr", name: "채용", inherited: true };

  it("폴더 「인사」를 고르고 검색하면 folderId를 전달한다", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} pending={false} folders={[hr, hiring]} />);

    const select = screen.getByLabelText("폴더");
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "전체 폴더" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "인사/채용" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "채용 절차" } });
    fireEvent.change(select, { target: { value: "f-hr" } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));

    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({ folderId: "f-hr" }));
  });

  it("폴더가 없으면 폴더 칸을 그리지 않는다", () => {
    render(<SearchForm onSearch={vi.fn()} pending={false} folders={[]} />);
    expect(screen.queryByLabelText("폴더")).not.toBeInTheDocument();
  });
});
