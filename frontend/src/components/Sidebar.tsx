// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getHealth, logout } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { ThemeToggle } from "./ThemeToggle";

const menus = [
  ["/", "홈", "M3 10 12 3l9 7v11h-6v-7H9v7H3Z"],
  ["/documents", "문서", "M6 3h8l4 4v14H6Z M14 3v5h4"],
  ["/search", "AI 검색", "m12 3 2 7 7 2-7 2-2 7-2-7-7-2 7-2Z"],
  ["/departments", "부서", "M4 21V3h10v18 M14 9h6v12 M7 7h4 M7 11h4 M7 15h4"],
  ["/clusters", "관계 지도", "M6 6l12 12 M18 6 6 18 M3 3h6v6H3Z M15 3h6v6h-6Z M9 15h6v6H9Z"],
  ["/organize", "정리할 문서", "m3 14 3-8h12l3 8v6H3Z M3 14h6v3h6v-3h6"],
  ["/diagnostics", "문서 진단", "M2 12h5l3-8 4 16 3-8h5"],
];
const adminMenus = [
  ["/admin/users", "사용자 관리", "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75"],
  ["/admin/groups", "그룹 관리", "M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z"],
  ["/admin/shares", "외부 공유", "M10 13a5 5 0 0 0 7 .5l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7-.5l-3 3a5 5 0 0 0 7 7l2-2"],
  ["/admin/audit", "감사 로그", "M9 3H6a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-3 M9 2h6v4H9Z M8 11h8 M8 16h5"],
  ["/admin/status", "시스템 상태", "M4 3h16a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z M4 14h16a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1Z M7 6h.01 M7 17h.01"],
];

export function Sidebar(): React.ReactElement {
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


  const pathname = usePathname();
  const [health, setHealth] = useState<boolean | null>(null);
  const [host, setHost] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal).then(() => {
      if (!controller.signal.aborted) {
        setHost(window.location.host);
        setHealth(true);
      }
    }).catch(() => {
      if (!controller.signal.aborted) {
        setHost(window.location.host);
        setHealth(false);
      }
    });
    return () => controller.abort();
  }, []);

  function menu(href: string, label: string, path: string = "M4 4h16v16H4Z"): React.ReactElement {
    const active = pathname === href || (href !== "/" && pathname.startsWith(href + "/"));
    return <Link key={href} href={href} aria-current={active ? "page" : undefined}
      className={`flex min-w-0 items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${active ? "bg-accent-soft font-semibold text-accent" : "text-body hover:text-accent"}`}>
      <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d={path} /></svg>
      {label}
    </Link>;
  }

  return (
    <aside className="flex w-full shrink-0 flex-col gap-6 border-b border-line bg-sunken p-4 lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:overflow-y-auto lg:border-r lg:border-b-0">
      <Link href="/" className="flex items-center gap-3 p-2 font-bold text-strong">
        <span className="flex size-8 items-center justify-center rounded-md bg-strong text-xs text-on-strong">OA</span>
        OpenArchive
      </Link>
      <nav aria-label="주요 메뉴" className="flex flex-wrap gap-1 lg:flex-col">
        {menus.filter(([href]) => href !== "/organize" || auth.authenticated).map(([href, label, path]) => menu(href, label, path))}
        {auth.is_admin ? <div className="w-full space-y-1 lg:mt-4">
          <p className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-muted">관리 <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent">관리자 전용</span></p>
          <div className="flex flex-wrap gap-1 lg:flex-col">{adminMenus.map(([href, label, path]) => menu(href, label, path))}</div>
        </div> : null}
        {auth.manages_department ? <div className="w-full space-y-1 lg:mt-4">
          <p className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-muted">관리 <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent">부서 관리자</span></p>
          {/* 부서 화면이 내 부서를 먼저 고르고 관리 도구를 연다 — 주요 메뉴의 「부서」와 겹치게 현재 표시는 하지 않는다. */}
          <Link href="/departments" className="flex min-w-0 items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-body hover:text-accent">
            <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6Z M9 12l2 2 4-4" /></svg>
            내 부서 관리
          </Link>
        </div> : null}
      </nav>
      <div className="space-y-4 lg:mt-auto">
        <div className="rounded-xl border border-line bg-card p-3 text-xs">
          <p className="flex items-center gap-2 font-medium text-strong">
            <span aria-hidden="true" className={`size-2 rounded-full ${health === true ? "bg-done" : health === false ? "bg-failed" : "bg-wait"}`} />
            {health === true ? "서버 연결됨" : health === false ? "서버 응답 없음" : "서버 확인 중"}
          </p>
          <p className="mt-2 break-all text-muted">{host}</p>
        </div>
        {!loading && auth.authenticated ? <>
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">{Array.from(auth.username ?? "")[0]}</span>
            <div className="min-w-0 flex-1 break-words" data-testid="account-label">
              <Link href="/settings" className="text-strong hover:text-accent">{auth.username}</Link>
              {auth.department ? <div className="text-xs text-muted">{auth.department.folder_id === null ? auth.department.name :
                <Link href={`/documents?folder=${encodeURIComponent(auth.department.folder_id)}`} className="hover:text-accent">{auth.department.name}</Link>}</div> : null}
            </div>
            <button type="button" disabled={loggingOut} onClick={() => void handleLogout()} className="shrink-0 text-xs text-muted hover:text-strong disabled:text-faint">로그아웃</button>
          </div>
        </> : !loading ? <Link href="/login" className="text-sm text-muted hover:text-accent">로그인</Link> : null}
        <div className="text-xs"><ThemeToggle /></div>
      </div>
    </aside>
  );
}
