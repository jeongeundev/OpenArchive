// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, getHealth, login } from "@/lib/api";
import { clearPasswordChangedNotice, wasPasswordChanged } from "@/lib/passwordChangeNotice";

export default function LoginPage(): React.ReactElement {
  const router = useRouter();
  const { setAuth } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 서버 프리렌더에는 sessionStorage가 없다. 이 안내는 클라이언트 내비게이션으로
  // 밀려온 렌더에서만 뜬다.
  const [passwordChanged] = useState(
    () => typeof window !== "undefined" && wasPasswordChanged(),
  );

  useEffect(clearPasswordChangedNotice, []);

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

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const status = await login(username, password);
      setAuth(status);
      router.replace("/");
    } catch (reason: unknown) {
      // 잠금(429)만 서버 안내를 보인다. 나머지는 실패 사유를 가리지 않고 한 문구로 둔다.
      setError(
        reason instanceof ApiError && reason.status === 429
          ? reason.detail
          : "사용자명 또는 비밀번호를 확인하세요.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen min-w-0 lg:grid-cols-2">
      <section className="flex min-w-0 flex-col bg-strong p-8 text-on-strong lg:p-16" aria-label="제품 소개">
        <div className="flex items-center gap-3 text-xl font-bold">
          <span className="flex size-9 items-center justify-center rounded-lg border border-current text-xs">OA</span>
          OpenArchive
        </div>
        <div className="mt-10 lg:mt-auto lg:pt-24">
          <h2 className="text-4xl leading-tight font-bold"><span className="block">조직의 문서를,</span><span className="block">질문 하나로 찾습니다.</span></h2>
          <p className="mt-6 text-sm leading-relaxed opacity-70">우리 서버, 우리 데이터베이스 위에서 동작하는 AI 문서관리 플랫폼</p>
          <ul className="mt-8 space-y-5 lg:mt-12">
            {[
              ["M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z M15 15l6 6", "의미 기반 AI 검색", "HWPX·PDF·XLSX 본문을 임베딩해 질문으로 찾고, 근거 구절을 함께 보여줍니다."],
              ["M4 21V3h10v18 M14 9h6v12 M7 7h4 M7 11h4", "부서·폴더 단위 권한", "열람 범위는 폴더를 따르고, 필요한 문서만 따로 부여합니다."],
              ["M6 6l12 12 M18 6 6 18 M3 3h6v6H3Z M15 3h6v6h-6Z M9 15h6v6H9Z", "문서 관계 지도", "같은 주제의 문서와 근거 기준을 연결해 보여줍니다."],
            ].map(([path, title, description]) => <li key={title} className="flex gap-4">
              <svg aria-hidden="true" className="mt-1 size-6 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d={path} /></svg>
              <div className="min-w-0"><h3 className="text-sm font-bold">{title}</h3><p className="mt-1 text-xs leading-relaxed opacity-70">{description}</p></div>
            </li>)}
          </ul>
        </div>
      </section>
      <div className="flex min-w-0 items-center px-8 py-12 lg:px-16">
      <section className="mx-auto w-full max-w-sm space-y-6">
      <h1 className="text-4xl font-semibold text-strong">로그인</h1>
      <p className="text-sm text-muted">관리자에게 발급받은 계정으로 로그인하세요.</p>
      {passwordChanged ? (
        <p className="rounded-lg border border-accent bg-card px-4 py-3 text-sm text-body" role="status">
          비밀번호를 바꿨습니다. 이 계정으로 열려 있던 모든 기기의 로그인이 끊겼으니 새
          비밀번호로 다시 로그인하세요. 발급한 API 토큰은 그대로 유효합니다.
        </p>
      ) : null}
      <form className="space-y-6" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm text-muted">
          아이디
          <input className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-body" autoComplete="username" onChange={(event) => setUsername(event.target.value)} type="text" value={username} />
        </label>
        <label className="block text-sm text-muted">
          비밀번호
          <input className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-body" autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} type="password" value={password} />
        </label>
        {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
        <button className="w-full rounded-lg bg-strong px-4 py-3 text-sm text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint" disabled={submitting} type="submit">
          {submitting ? "로그인 중…" : "로그인"}
        </button>
      </form>
      <p className="text-xs leading-relaxed text-muted">비밀번호를 잊었으면 관리자에게 재설정을 요청하세요.</p>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-card p-3 text-xs text-muted" role="status">
        <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${health === true ? "bg-done" : health === false ? "bg-failed" : "bg-wait"}`} />
        <span>{health === true ? "서버 연결됨" : health === false ? "서버 응답 없음" : "서버 확인 중"}</span>
        <span className="break-all">{host}</span>
      </div>
    </section>
    </div>
    </div>
  );
}
