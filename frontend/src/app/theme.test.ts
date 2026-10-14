// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "globals.css"), "utf8");
const layout = readFileSync(join(__dirname, "layout.tsx"), "utf8");
const globalError = readFileSync(join(__dirname, "global-error.tsx"), "utf8");

// 결정 T1의 표 — [토큰, 라이트, 다크]
const TOKENS: [string, string, string][] = [
  ["page", "#ffffff", "#0a0a0a"],
  ["card", "#ffffff", "#141414"],
  ["sunken", "#f3f4f6", "#262626"],
  ["line", "#e5e7eb", "#262626"],
  ["line-strong", "#d1d5db", "#404040"],
  ["strong", "#111827", "#ffffff"],
  ["body", "#374151", "#d4d4d4"],
  ["muted", "#6b7280", "#a3a3a3"],
  ["faint", "#9ca3af", "#525252"],
  ["on-strong", "#ffffff", "#000000"],
  ["accent", "#1d4ed8", "#0ea5e9"],
  ["accent-soft", "#eff6ff", "rgb(14 165 233 / 0.1)"],
  ["done", "#047857", "#22c55e"],
  ["done-soft", "#ecfdf5", "rgb(34 197 94 / 0.1)"],
  ["failed", "#b91c1c", "#ef4444"],
  ["failed-soft", "#fef2f2", "rgb(239 68 68 / 0.1)"],
  ["wait", "#6b7280", "#a3a3a3"],
  ["wait-soft", "#f3f4f6", "rgb(163 163 163 / 0.1)"],
  ["attention", "#b45309", "#f59e0b"],
  ["attention-soft", "#fffbeb", "rgb(245 158 11 / 0.1)"],
];

/** `selector { … }` 블록의 본문. 중첩 없는 블록만 다룬다. */
function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} 블록이 없다`).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

function variable(body: string, name: string): string | undefined {
  const m = body.match(new RegExp(`--${name}:\\s*([^;]+);`));
  return m?.[1].trim();
}

describe("globals.css 의미 토큰", () => {
  it("@theme가 토큰 20개를 --color-*로 노출하고 테마 변수를 가리킨다", () => {
    const theme = block("@theme inline");
    for (const [name] of TOKENS) {
      expect(variable(theme, `color-${name}`), name).toBe(`var(--oa-${name})`);
    }
  });

  it("라이트 값은 :root, 다크 값은 data-theme=dark에 있다", () => {
    const light = block(":root");
    const dark = block('[data-theme="dark"]');
    for (const [name, lightValue, darkValue] of TOKENS) {
      expect(variable(light, `oa-${name}`), name).toBe(lightValue);
      expect(variable(dark, `oa-${name}`), name).toBe(darkValue);
    }
  });

  it("테마는 data-theme 하나로만 갈린다 — prefers-color-scheme 미디어 쿼리가 없다", () => {
    expect(css).not.toMatch(/@media[^{]*prefers-color-scheme/);
  });

  it("포커스 링·움직임 줄이기 규칙이 있고 그림자·그라데이션이 없다", () => {
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-accent\)/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(css).not.toMatch(/box-shadow|gradient/);
  });

  it("글꼴은 시스템 스택이다 — geist를 쓰지 않는다", () => {
    expect(layout).not.toMatch(/geist/i);
    expect(globalError).not.toMatch(/geist/i);
    expect(variable(block("@theme inline"), "font-sans")).toContain("Apple SD Gothic Neo");
  });

  it("두 레이아웃 모두 그리기 전에 테마를 정하는 스크립트를 넣는다", () => {
    for (const source of [layout, globalError]) {
      expect(source).toContain("THEME_BOOT_SCRIPT");
      expect(source).toContain("suppressHydrationWarning");
    }
  });
});
