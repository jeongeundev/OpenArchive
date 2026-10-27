// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
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
    inherited: false, can_manage: false, can_change_access: false, can_share: false,
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

  it("검색어와 검색 버튼을 한 줄에, 필터 넷을 그 아래 왼쪽 열에 둔다", () => {
    render(<SearchForm onSearch={vi.fn()} pending={false} folders={[hr]} />);

    const queryRow = screen.getByTestId("search-query-row");
    expect(within(queryRow).getByLabelText("검색어")).toBeInTheDocument();
    expect(within(queryRow).getByRole("button", { name: "검색" })).toBeInTheDocument();

    const filterRow = screen.getByTestId("search-filter-row");
    for (const label of ["태그 (쉼표로 구분)", "문서 유형", "결과 수", "폴더"]) {
      expect(within(filterRow).getByLabelText(label)).toBeInTheDocument();
    }
    expect(within(filterRow).queryByLabelText("검색어")).not.toBeInTheDocument();
    expect(screen.getByText(/폴더는 직접 맞은 결과에만 걸립니다/)).toHaveClass("text-xs");
    expect(screen.getByTestId("search-columns")).toHaveClass("lg:grid-cols-[14rem_minmax(0,1fr)]");
    expect(screen.getByTestId("search-columns").firstElementChild).toContainElement(filterRow);
  });

  it("검색어 라벨은 화면에서 숨겨도 접근 이름으로 남고, 입력을 강조 테두리로 둔다", () => {
    render(<SearchForm onSearch={vi.fn()} pending={false} />);

    expect(screen.getByText("검색어")).toHaveClass("sr-only");
    expect(screen.getByLabelText("검색어")).toHaveClass("border-accent");
    expect(screen.getByRole("button", { name: "검색" })).toHaveClass("bg-accent", "text-on-strong");
  });

  it("결과는 필터 열 오른쪽 단에 그리고, 필터를 바꿔도 「검색」을 눌러야 검색한다", () => {
    const onSearch = vi.fn();
    render(
      <SearchForm onSearch={onSearch} pending={false} folders={[hr]}>
        <p>결과 자리</p>
      </SearchForm>,
    );

    const [, results] = Array.from(screen.getByTestId("search-columns").children);
    expect(results).toHaveTextContent("결과 자리");
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "채용" } });
    fireEvent.change(screen.getByLabelText("문서 유형"), { target: { value: "pdf" } });
    expect(onSearch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({ query: "채용", contentType: "pdf" }));
  });

  it("왼쪽 열의 필터 칸은 검색 폼에 속한다 — 필터 칸에서 Enter로도 검색한다", () => {
    render(<SearchForm onSearch={vi.fn()} pending={false} folders={[hr]} />);

    const form = screen.getByLabelText("검색어").closest("form");
    for (const label of ["태그 (쉼표로 구분)", "문서 유형", "결과 수", "폴더"]) {
      expect(screen.getByLabelText(label)).toHaveProperty("form", form);
    }
  });
});
