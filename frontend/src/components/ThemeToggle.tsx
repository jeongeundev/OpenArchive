// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useSyncExternalStore } from "react";

import { applyTheme, storeTheme, type Theme } from "@/lib/theme";

/** 현재 테마의 출처는 `html[data-theme]` 하나다 — 처음 값은 `<head>`의 부트 스크립트가 정해 둔다. */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributeFilter: ["data-theme"], attributes: true });
  return () => observer.disconnect();
}

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/** 정적 산출물에는 data-theme이 없고 CSS 기본값이 라이트다. */
function serverTheme(): Theme {
  return "light";
}

/** 헤더의 테마 버튼. 글자는 바꿀 대상 테마의 이름이다. */
export function ThemeToggle(): React.ReactElement {
  const theme = useSyncExternalStore(subscribe, currentTheme, serverTheme);

  function toggle(): void {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    storeTheme(next);
  }

  return (
    <button
      aria-pressed={theme === "dark"}
      className="text-muted hover:text-strong"
      onClick={toggle}
      type="button"
    >
      {theme === "dark" ? "라이트 테마" : "다크 테마"}
    </button>
  );
}
