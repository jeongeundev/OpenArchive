// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  THEME_BOOT_SCRIPT, THEME_STORAGE_KEY, applyTheme, initialTheme, readStoredTheme, storeTheme,
} from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("initialTheme", () => {
  it("저장된 선택이 운영체제 설정보다 이긴다", () => {
    expect(initialTheme("light", true)).toBe("light");
    expect(initialTheme("dark", false)).toBe("dark");
  });

  it("저장값이 없거나 알 수 없으면 운영체제 설정을 따른다", () => {
    expect(initialTheme(null, true)).toBe("dark");
    expect(initialTheme(null, false)).toBe("light");
    expect(initialTheme("blue", true)).toBe("dark");
  });
});

describe("저장과 적용", () => {
  it("선택을 같은 키로 저장하고 읽는다", () => {
    storeTheme("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(readStoredTheme()).toBe("dark");
  });

  it("localStorage가 막혀도 던지지 않는다", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => storeTheme("light")).not.toThrow();
    expect(readStoredTheme()).toBeNull();
  });

  it("applyTheme은 html의 data-theme을 바꾼다", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

describe("THEME_BOOT_SCRIPT", () => {
  it("같은 키와 운영체제 설정을 쓴다", () => {
    expect(THEME_BOOT_SCRIPT).toContain(THEME_STORAGE_KEY);
    expect(THEME_BOOT_SCRIPT).toContain("matchMedia('(prefers-color-scheme: dark)')");
  });

  function run(stored: string | null, prefersDark: boolean): string | undefined {
    localStorage.clear();
    if (stored !== null) localStorage.setItem(THEME_STORAGE_KEY, stored);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: prefersDark && q.includes("dark") }));
    new Function(THEME_BOOT_SCRIPT)();
    vi.unstubAllGlobals();
    return document.documentElement.dataset.theme;
  }

  it("initialTheme과 같은 규칙으로 data-theme을 정한다", () => {
    expect(run("light", true)).toBe("light");
    expect(run(null, true)).toBe("dark");
    expect(run("blue", false)).toBe("light");
  });

  it("localStorage가 막히면 운영체제 설정으로 정한다", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(run(null, true)).toBe("dark");
  });
});
