import type { Metadata } from "next";
// 글꼴은 패키지에 든 파일을 쓴다. next/font/google은 빌드 때마다 Google Fonts 응답을 받아와
// 응답이 바뀌면(unicode-range 등) 소스가 그대로여도 동봉 산출물이 달라진다.
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { SiteHeader } from "@/components/SiteHeader";
import { SiteFooter } from "@/components/SiteFooter";
import { AuthProvider } from "@/components/AuthProvider";
import { RequireAuth } from "@/components/RequireAuth";
import { RetryNotice } from "@/components/RetryNotice";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenArchive",
  description: "OpenSQL 기반 AI 문서관리 플랫폼",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
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
