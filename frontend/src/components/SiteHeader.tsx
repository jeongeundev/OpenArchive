"use client";

import Link from "next/link";
import { useState } from "react";

import { logout } from "@/lib/api";
import { useAuth } from "./AuthProvider";

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
    <header className="border-b border-neutral-800 bg-[#0a0a0a]">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-4">
        <div className="flex items-center gap-6">
          <Link className="font-semibold text-white" href="/">OpenArchive</Link>
          <nav aria-label="주요 메뉴" className="flex items-center gap-4 text-sm">
            <Link className="text-neutral-400 hover:text-[#0ea5e9]" href="/">문서</Link>
            <Link className="text-neutral-400 hover:text-[#0ea5e9]" href="/search">검색</Link>
            {auth.is_admin ? (
              <Link className="text-neutral-400 hover:text-[#0ea5e9]" href="/admin/users">
                사용자 관리
              </Link>
            ) : null}
          </nav>
        </div>
        {!loading && auth.authenticated ? (
          <div className="flex items-center gap-3 text-sm">
            <span className="text-neutral-400">{auth.username}</span>
            <button
              className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
              disabled={loggingOut}
              onClick={() => void handleLogout()}
              type="button"
            >
              로그아웃
            </button>
          </div>
        ) : !loading ? (
          <Link className="text-sm text-neutral-400 hover:text-[#0ea5e9]" href="/login">
            로그인
          </Link>
        ) : null}
      </div>
    </header>
  );
}
