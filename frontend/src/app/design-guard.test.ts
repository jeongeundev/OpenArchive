// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// 결정 T5: 화면·컴포넌트는 의미 토큰 유틸리티만 쓴다. 원시 색·그림자·흐림·그라데이션·보라 계열이
// 다시 들어오면 파일:줄로 알린다. 허용 목록은 두지 않는다.
const SRC = join(__dirname, "..");

const FORBIDDEN: [string, RegExp][] = [
  ["원시 회색 팔레트", /\b(?:[a-z]+:)*[a-z]+-(?:neutral|gray|zinc|slate|stone)-\d{2,3}\b/],
  ["흰색·검정 클래스", /\b(?:text|bg|border|fill|stroke|divide|ring|outline|placeholder)-(?:white|black)\b/],
  ["원시 색 팔레트", /\b[a-z]+-(?:red|green|sky|blue|amber|emerald|yellow|orange|rose|teal|cyan|lime)-\d{2,3}\b/],
  ["대괄호 hex 색", /-\[#[0-9a-fA-F]{3,8}\]/],
  ["속성 hex 색", /"#[0-9a-fA-F]{3,8}"/],
  ["그림자", /\bshadow\b|\bshadow-/],
  ["흐림", /\bblur\b|\bblur-/],
  ["backdrop", /\bbackdrop-/],
  ["그라데이션", /\bbg-gradient/],
  ["보라 계열", /\b(?:indigo|violet|purple)\b|-(?:indigo|violet|purple)-/],
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return name.endsWith(".tsx") && !name.endsWith(".test.tsx") ? [path] : [];
  });
}

describe("디자인 가드 (T5)", () => {
  it("화면·컴포넌트에 원시 색·그림자·흐림·그라데이션·보라 계열이 없다", () => {
    const violations: string[] = [];
    for (const file of sourceFiles(SRC)) {
      readFileSync(file, "utf8").split("\n").forEach((line, i) => {
        for (const [label, pattern] of FORBIDDEN) {
          const match = line.match(pattern);
          if (match) violations.push(`${relative(SRC, file)}:${i + 1} ${label} — ${match[0]}`);
        }
      });
    }
    expect(violations, violations.join("\n")).toEqual([]);
  });

  it("globals.css가 의미 토큰을 모두 정의한다", () => {
    const css = readFileSync(join(__dirname, "globals.css"), "utf8");
    const tokens = [
      "page", "card", "sunken", "line", "line-strong", "strong", "body", "muted", "faint", "on-strong",
      "accent", "accent-soft", "done", "done-soft", "failed", "failed-soft", "wait", "wait-soft",
      "attention", "attention-soft",
    ];
    for (const token of tokens) expect(css).toContain(`--color-${token}:`);
  });

  it("본문·푸터가 같은 폭(max-w-[96rem])을 쓴다 — 1920 화면에서 가장자리가 어긋나지 않고 목록 정보 창이 열려도 가운데 열이 좁지 않게", () => {
    const shells = [
      "components/AppShell.tsx",
      "app/global-error.tsx",

      "components/SiteFooter.tsx",
    ];
    const sidebar = readFileSync(join(SRC, "components/Sidebar.tsx"), "utf8");
    expect(sidebar).not.toMatch(/\bmax-w-(?:xs|sm|md|lg|xl|[2-7]xl|full|screen|none)\b/);
    for (const file of shells) {
      const source = readFileSync(join(SRC, file), "utf8");
      expect(source, file).toContain("max-w-[96rem]");
      expect(source, file).not.toMatch(/\bmax-w-(?:xs|sm|md|lg|xl|[2-7]xl|full|screen|none)\b/);
    }
  });
});
