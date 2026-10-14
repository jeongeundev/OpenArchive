// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { THEME_BOOT_SCRIPT } from "@/lib/theme";
import "./globals.css";

/**
 * 루트 레이아웃(헤더·로그인 처리)에서 난 오류의 경계 — `error.tsx`는 이 자리를 잡지 못한다.
 * 레이아웃을 통째로 대체하므로 문서와 전역 스타일을 직접 갖는다. 오류 내용은 화면에 보이지 않는다.
 * 목록 링크는 `<Link>`가 아닌 `<a>`다 — 레이아웃이 깨진 상태라 처음부터 다시 불러온다.
 */
export default function GlobalError({ retry }: {
  error: Error & { digest?: string };
  retry: () => void;
  reset: () => void;
}): React.ReactElement {
  return (
    <html lang="ko" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">
        <title>OpenArchive</title>
        <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
          <section className="space-y-4">
            <h1 className="text-4xl font-semibold text-white">화면을 표시하지 못했습니다</h1>
            <p className="text-sm text-neutral-400">잠시 뒤 다시 시도하세요. 문제가 계속되면 관리자에게 알려 주세요.</p>
            <div className="flex items-center gap-4">
              <button type="button" onClick={() => retry()}
                className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black hover:bg-neutral-200">
                다시 시도
              </button>
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
              <a href="/" className="text-sm text-[#0ea5e9] hover:underline">문서 목록으로 가기</a>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}
