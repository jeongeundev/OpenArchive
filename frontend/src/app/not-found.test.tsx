// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import NotFound from "./not-found";

it("없는 주소는 한국어로 알리고 문서 목록으로 돌아갈 링크를 준다", () => {
  render(<NotFound />);

  expect(screen.getByRole("heading", { name: "페이지를 찾을 수 없습니다" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "문서 목록으로 가기" })).toHaveAttribute("href", "/");
});
