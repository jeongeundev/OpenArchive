// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import ErrorPage from "./error";

it("화면 오류는 한국어로 알리고 내부 오류 내용을 보이지 않는다", () => {
  const error = Object.assign(new Error("relation \"documents\" does not exist at db.py:42"), { digest: "1234567" });
  const { container } = render(<ErrorPage error={error} retry={vi.fn()} reset={vi.fn()} />);

  expect(screen.getByRole("heading", { name: "화면을 표시하지 못했습니다" })).toBeInTheDocument();
  expect(container).not.toHaveTextContent("relation");
  expect(container).not.toHaveTextContent("db.py");
  expect(container).not.toHaveTextContent("1234567");
  expect(screen.getByRole("link", { name: "문서 목록으로 가기" })).toHaveAttribute("href", "/");
});

it("「다시 시도」를 누르면 그 화면을 다시 불러온다", () => {
  const retry = vi.fn();
  render(<ErrorPage error={new Error("boom")} retry={retry} reset={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  expect(retry).toHaveBeenCalledOnce();
});
