// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useState } from "react";

import { logout } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { ThemeToggle } from "./ThemeToggle";

export function SiteHeader(): React.ReactElement {
  const { auth, loading, setAuth } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout(): Promise<void> {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      setAuth(await logout());
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <header className="border-b border-line bg-page">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-4">
        <div className="flex items-center gap-6">
          <Link className="font-semibold text-strong" href="/">OpenArchive</Link>
          <nav aria-label="주요 메뉴" className="flex items-center gap-4 text-sm">
            <Link className="text-muted hover:text-accent" href="/">문서</Link>
            <Link className="text-muted hover:text-accent" href="/search">검색</Link>
            <Link className="text-muted hover:text-accent" href="/clusters">관계 지도</Link>
            {auth.authenticated ? (
              <Link className="text-muted hover:text-accent" href="/organize">정리할 문서</Link>
            ) : null}
            <Link className="text-muted hover:text-accent" href="/diagnostics">문서 진단</Link>
            {auth.is_admin ? (
              <>
                <Link className="text-muted hover:text-accent" href="/admin/users">
                  사용자 관리
                </Link>
                <Link className="text-muted hover:text-accent" href="/admin/groups">
                  그룹 관리
                </Link>
                <Link className="text-muted hover:text-accent" href="/admin/shares">
                  외부 공유
                </Link>
                <Link className="text-muted hover:text-accent" href="/admin/audit">
                  감사 로그
                </Link>
                <Link className="text-muted hover:text-accent" href="/admin/status">
                  시스템 상태
                </Link>
              </>
            ) : null}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {!loading && auth.authenticated ? (
            <>
              <Link className="text-muted hover:text-accent" href="/settings">
                {auth.username}
              </Link>
              <button
                className="text-muted hover:text-strong disabled:text-faint"
                disabled={loggingOut}
                onClick={() => void handleLogout()}
                type="button"
              >
                로그아웃
              </button>
            </>
          ) : !loading ? (
            <Link className="text-sm text-muted hover:text-accent" href="/login">
              로그인
            </Link>
          ) : null}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
