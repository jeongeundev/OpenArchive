import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { SUPPORTED_CONTENT_TYPES } from "@/lib/types";
import { DocumentFilters } from "./DocumentFilters";

afterEach(() => vi.useRealTimers());

it("제목·유형·태그·정렬의 선택지를 표시한다", () => {
  render(<DocumentFilters value={{}} tags={["보안", "운영"]} onChange={vi.fn()} />);
  expect(screen.getByRole("textbox", { name: "제목 검색" })).toHaveValue("");
  const types = within(screen.getByRole("combobox", { name: "문서 유형" }));
  expect(types.getByRole("option", { name: "전체" })).toHaveValue("");
  for (const type of SUPPORTED_CONTENT_TYPES) {
    expect(types.getByRole("option", { name: type.toUpperCase() })).toHaveValue(type);
  }
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
  render(<DocumentFilters value={value} tags={["보안", "운영"]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("문서 유형"), { target: { value: "hwp" } });
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
