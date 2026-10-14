// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY } from "@/lib/theme";
import { ThemeToggle } from "./ThemeToggle";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("ThemeToggle", () => {
  it("라이트에서는 다크 테마로 바꾸는 버튼이고, 누르면 다크로 바뀌어 저장된다", async () => {
    document.documentElement.dataset.theme = "light";
    render(<ThemeToggle />);

    const button = await screen.findByRole("button", { name: "다크 테마" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(button);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    const pressed = await screen.findByRole("button", { name: "라이트 테마" });
    expect(pressed).toHaveAttribute("aria-pressed", "true");
  });

  it("data-theme이 없으면 라이트로 본다", async () => {
    render(<ThemeToggle />);

    expect(await screen.findByRole("button", { name: "다크 테마" })).toBeInTheDocument();
  });

  it("다크에서 누르면 라이트로 돌아간다", async () => {
    document.documentElement.dataset.theme = "dark";
    render(<ThemeToggle />);

    fireEvent.click(await screen.findByRole("button", { name: "라이트 테마" }));

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("저장이 막혀도 그 탭에서는 테마가 바뀐다", async () => {
    document.documentElement.dataset.theme = "light";
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<ThemeToggle />);

    fireEvent.click(await screen.findByRole("button", { name: "다크 테마" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(await screen.findByRole("button", { name: "라이트 테마" })).toBeInTheDocument();
  });
});
