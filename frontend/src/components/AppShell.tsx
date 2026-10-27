// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { SiteFooter } from "./SiteFooter";
import { RetryNotice } from "./RetryNotice";
import { RequireAuth } from "./RequireAuth";

export function AppShell({ children }: { children: React.ReactNode }): React.ReactElement {
  const pathname = usePathname();
  return <div className="flex min-h-screen flex-col bg-page lg:flex-row">
    {pathname !== "/login" ? <Sidebar /> : null}
    <div className="flex min-w-0 flex-1 flex-col">
      <main className="mx-auto w-full max-w-[96rem] flex-1 px-8 py-10">
        <RetryNotice />
        <RequireAuth>{children}</RequireAuth>
      </main>
      <SiteFooter />
    </div>
  </div>;
}
