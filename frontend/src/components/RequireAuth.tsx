"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/AuthProvider";

/** 익명에게 열어 두는 경로. 여기서 되돌려 보내면 로그인할 방법이 사라진다. */
const PUBLIC_PATHS = new Set(["/login"]);

export function RequireAuth({ children }: { children: React.ReactNode }): React.ReactElement {
  const { auth, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.has(pathname);

  useEffect(() => {
    if (!loading && !auth.authenticated && !isPublic) router.replace("/login");
  }, [loading, auth.authenticated, isPublic, router]);

  // 익명은 아무 문서도 보지 못하므로(ADR-028) 화면도 그리지 않는다. 그리고 나서 지우면
  // 한 프레임이지만 목록 자리와 개수가 드러난다.
  if (isPublic || (!loading && auth.authenticated)) return <>{children}</>;
  return <p className="text-sm text-neutral-500">불러오는 중…</p>;
}
