// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";


import { DocumentFilters } from "./DocumentFilters";

afterEach(() => vi.useRealTimers());

it("제목·유형·태그·정렬의 선택지를 표시한다", () => {
  render(<DocumentFilters value={{}} types={["hwp", "pdf"]} tags={["보안", "운영"]} onChange={vi.fn()} />);
  expect(screen.getByRole("textbox", { name: "제목 검색" })).toHaveValue("");
  const types = within(screen.getByRole("group", { name: "문서 유형" }));
  for (const type of ["전체", "HWP", "PDF"]) expect(types.getByRole("button", { name: type })).toBeInTheDocument();
  expect(types.queryByRole("button", { name: "XLSX" })).not.toBeInTheDocument();
  const tags = within(screen.getByRole("combobox", { name: "태그" }));
  for (const tag of ["전체", "보안", "운영"]) expect(tags.getByRole("option", { name: tag })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "정렬" })).toHaveValue("updated");
  expect(screen.getByRole("option", { name: "최근 수정순" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "제목순" })).toBeInTheDocument();
});

it("선택 변경은 다른 조건을 보존하고 제목은 입력이 멈춘 뒤 한 번 전달한다", () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const value = { q: "기존", tag: "운영", sort: "updated" as const };
  render(<DocumentFilters value={value} types={["hwp", "pdf"]} tags={["보안", "운영"]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "HWP" }));
  expect(onChange).toHaveBeenLastCalledWith({ ...value, contentType: "hwp" });
  fireEvent.change(screen.getByLabelText("태그"), { target: { value: "보안" } });
  expect(onChange).toHaveBeenLastCalledWith({ ...value, tag: "보안" });
  fireEvent.change(screen.getByLabelText("정렬"), { target: { value: "title" } });
  expect(onChange).toHaveBeenLastCalledWith({ ...value, sort: "title" });
  onChange.mockClear();
  fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "출" } });
  act(() => vi.advanceTimersByTime(100));
  fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "출장" } });
  expect(onChange).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(300));
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...value, q: "출장" });
});

it("바깥에서 제목 조건이 바뀌면 입력칸이 따라가고 이전 입력을 되돌려 쓰지 않는다", () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const { rerender } = render(<DocumentFilters value={{ q: "출장", sort: "updated" }} tags={[]} onChange={onChange} />);
  rerender(<DocumentFilters value={{ sort: "updated" }} tags={[]} onChange={onChange} />);
  expect(screen.getByLabelText("제목 검색")).toHaveValue("");
  rerender(<DocumentFilters value={{ q: "교육", sort: "updated" }} tags={[]} onChange={onChange} />);
  expect(screen.getByLabelText("제목 검색")).toHaveValue("교육");
  act(() => vi.advanceTimersByTime(500));
  expect(onChange).not.toHaveBeenCalled();
});

it("입력이 반영된 뒤에도 이어서 친 글자를 지우지 않는다", () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const { rerender } = render(<DocumentFilters value={{ sort: "updated" }} tags={[]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "출장" } });
  act(() => vi.advanceTimersByTime(300));
  expect(onChange).toHaveBeenLastCalledWith({ sort: "updated", q: "출장" });
  fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "출장비" } });
  rerender(<DocumentFilters value={{ q: "출장", sort: "updated" }} tags={[]} onChange={onChange} />);
  expect(screen.getByLabelText("제목 검색")).toHaveValue("출장비");
});
