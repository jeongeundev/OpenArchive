// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import GlobalError from "./global-error";

// next/font는 Next 빌드 밖(vitest)에서 불러올 수 없다. 글꼴은 이 테스트의 대상이 아니다.
vi.mock("geist/font/sans", () => ({ GeistSans: { variable: "font-geist-sans" } }));
vi.mock("geist/font/mono", () => ({ GeistMono: { variable: "font-geist-mono" } }));

it("루트 레이아웃 오류는 자체 문서를 한국어로 그리고 내부 오류 내용을 보이지 않는다", () => {
  const error = Object.assign(new Error("Cannot read properties of undefined (reading 'username')"), { digest: "7654321" });
  const html = renderToStaticMarkup(<GlobalError error={error} retry={vi.fn()} reset={vi.fn()} />);

  expect(html).toMatch(/^<html lang="ko"/);
  expect(html).toContain("<body");
  expect(html).toContain("화면을 표시하지 못했습니다");
  expect(html).not.toContain("username");
  expect(html).not.toContain("7654321");
});

it("「다시 시도」로 다시 그리거나 문서 목록으로 갈 수 있다", () => {
  const retry = vi.fn();
  render(<GlobalError error={new Error("boom")} retry={retry} reset={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(screen.getByRole("link", { name: "문서 목록으로 가기" })).toHaveAttribute("href", "/documents");
});
