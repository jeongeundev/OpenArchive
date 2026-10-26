// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import { scopeSummary } from "./scope";

describe("scopeSummary", () => {
  it("폴더 범위를 따르지 않으면 실효 범위 이름만 낸다", () => {
    expect(scopeSummary({ effective_visibility: "public", follows_folder: false })).toBe("조직 공개");
    expect(scopeSummary({ effective_visibility: "private", follows_folder: false })).toBe("제한");
  });

  it("폴더 범위를 따르면 「 · 폴더 범위 따름」을 붙인다", () => {
    expect(scopeSummary({ effective_visibility: "public", follows_folder: true })).toBe(
      "조직 공개 · 폴더 범위 따름",
    );
    expect(scopeSummary({ effective_visibility: "private", follows_folder: true })).toBe(
      "제한 · 폴더 범위 따름",
    );
  });
});
