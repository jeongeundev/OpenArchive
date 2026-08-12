"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { login } from "@/lib/api";

export default function LoginPage(): React.ReactElement {
  const router = useRouter();
  const { setAuth } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const status = await login(username, password);
      setAuth(status);
      router.replace("/");
    } catch {
      setError("사용자명 또는 비밀번호를 확인하세요.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="max-w-md space-y-6">
      <h1 className="text-4xl font-semibold text-white">로그인</h1>
      <form className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm text-neutral-400">
          사용자명
          <input className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" autoComplete="username" onChange={(event) => setUsername(event.target.value)} type="text" value={username} />
        </label>
        <label className="block text-sm text-neutral-400">
          비밀번호
          <input className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} type="password" value={password} />
        </label>
        {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
        <button className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400" disabled={submitting} type="submit">
          {submitting ? "로그인 중…" : "로그인"}
        </button>
      </form>
    </section>
  );
}
