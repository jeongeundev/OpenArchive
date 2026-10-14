// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { Metadata } from "next";
import { SiteHeader } from "@/components/SiteHeader";
import { SiteFooter } from "@/components/SiteFooter";
import { AuthProvider } from "@/components/AuthProvider";
import { RequireAuth } from "@/components/RequireAuth";
import { RetryNotice } from "@/components/RetryNotice";
import { THEME_BOOT_SCRIPT } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenArchive",
  description: "OpenSQL 기반 AI 문서관리 플랫폼",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // 글꼴은 시스템 스택(globals.css)이다. 테마는 그리기 전에 아래 스크립트가 data-theme으로 정하므로
    // 서버 산출물과 html 속성이 달라진다 — suppressHydrationWarning.
    <html lang="ko" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">
        <AuthProvider>
          <SiteHeader />
          <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
            <RetryNotice />
            <RequireAuth>{children}</RequireAuth>
          </main>
          <SiteFooter />
        </AuthProvider>
      </body>
    </html>
  );
}
