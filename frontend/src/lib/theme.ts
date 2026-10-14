// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT

/**
 * 라이트·다크 테마 선택. 처음 방문은 운영체제 설정을 따르고, 사용자가 고르면 localStorage에 남는다.
 * 색은 CSS가 `html[data-theme]` 하나로만 가른다(globals.css).
 */
export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "openarchive-theme";

export function initialTheme(stored: string | null, prefersDark: boolean): Theme {
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark ? "dark" : "light";
}

/** 개인 정보 보호 모드 등에서 localStorage가 막혀도 던지지 않는다. */
export function readStoredTheme(): string | null {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 저장에 실패해도 그 탭에서는 바뀐다 — 다음 방문에 남지 않을 뿐이다. */
export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // 저장하지 못해도 화면은 이미 바뀌었다.
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/**
 * 첫 화면 깜빡임을 막으려고 `<head>`에서 그리기 전에 실행하는 스크립트. initialTheme과 같은 규칙이다.
 * 번들 전에 돌기 때문에 위 함수를 쓰지 못하고 ES5로 다시 적는다. 실패하면 CSS 기본값(라이트)이 남는다.
 */
export const THEME_BOOT_SCRIPT =
  "(function(){var s=null;try{s=localStorage.getItem('" + THEME_STORAGE_KEY + "')}catch(e){}" +
  "try{var t=s==='light'||s==='dark'?s:" +
  "(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');" +
  "document.documentElement.dataset.theme=t}catch(e){}})();";
