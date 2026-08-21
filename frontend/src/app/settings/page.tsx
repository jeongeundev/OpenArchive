"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import {
  ApiError,
  changePassword,
  createToken,
  listTokens,
  revokeToken,
} from "@/lib/api";
import type { TokenCreated, TokenScope, TokenSummary } from "@/lib/types";

const SCOPE_LABEL: Record<TokenScope, string> = {
  read: "읽기 전용",
  read_write: "읽기·쓰기",
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function SettingsPage(): React.ReactElement {
  const { auth, loading: authLoading, setAuth } = useAuth();
  const [tokens, setTokens] = useState<TokenSummary[]>([]);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<TokenScope>("read");
  const [issued, setIssued] = useState<TokenCreated | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [tokenWorking, setTokenWorking] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordWorking, setPasswordWorking] = useState(false);
  const [passwordChanged, setPasswordChanged] = useState(false);

  useEffect(() => {
    if (authLoading || !auth.authenticated) return;
    let active = true;
    listTokens()
      .then((items) => {
        if (active) setTokens(items);
      })
      .catch((reason: unknown) => {
        if (active) {
          setTokenError(
            reason instanceof ApiError ? reason.detail : "토큰 목록을 불러오지 못했습니다.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [auth.authenticated, authLoading]);

  async function issue(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (tokenWorking) return;
    setTokenWorking(true);
    setTokenError(null);
    try {
      setIssued(await createToken({ name, scope }));
      setName("");
      setTokens(await listTokens());
    } catch (reason: unknown) {
      setTokenError(reason instanceof ApiError ? reason.detail : "토큰을 발급하지 못했습니다.");
    } finally {
      setTokenWorking(false);
    }
  }

  async function revoke(token: TokenSummary): Promise<void> {
    if (!window.confirm(`'${token.name}' 토큰을 폐기하시겠습니까? 이 토큰을 쓰는 프로그램은 즉시 401을 받습니다.`)) {
      return;
    }
    setTokenWorking(true);
    setTokenError(null);
    try {
      await revokeToken(token.id);
      if (issued?.id === token.id) setIssued(null);
      setTokens(await listTokens());
    } catch (reason: unknown) {
      setTokenError(reason instanceof ApiError ? reason.detail : "토큰을 폐기하지 못했습니다.");
    } finally {
      setTokenWorking(false);
    }
  }

  async function submitPassword(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (passwordWorking) return;
    setPasswordWorking(true);
    setPasswordError(null);
    try {
      setAuth(await changePassword(currentPassword, newPassword));
      setPasswordChanged(true);
    } catch (reason: unknown) {
      setPasswordError(
        reason instanceof ApiError ? reason.detail : "비밀번호를 바꾸지 못했습니다.",
      );
    } finally {
      setPasswordWorking(false);
    }
  }

  if (passwordChanged) {
    return (
      <section className="space-y-4">
        <h1 className="text-4xl font-semibold text-white">비밀번호를 바꿨습니다</h1>
        <p className="text-sm text-neutral-400">
          이 계정으로 열려 있던 모든 기기의 로그인이 끊겼습니다. 새 비밀번호로 다시 로그인하세요.
        </p>
        <p className="text-sm text-neutral-500">
          발급된 API 토큰은 그대로 유효합니다. 필요하면 다시 로그인해 이 화면에서 폐기하세요.
        </p>
        <Link className="inline-block text-sm text-[#0ea5e9] hover:text-white" href="/login">
          로그인 화면으로
        </Link>
      </section>
    );
  }

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.authenticated) return <p className="text-sm text-neutral-500">로그인이 필요합니다.</p>;

  return (
    <section className="space-y-10">
      <div>
        <h1 className="text-4xl font-semibold text-white">계정 설정</h1>
        <p className="mt-3 text-sm text-neutral-400">
          {auth.username} 계정의 API 토큰과 비밀번호를 관리합니다.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-white">API 토큰</h2>
          <p className="mt-2 text-sm text-neutral-400">
            프로그램이 이 저장소에 문서를 공급하거나 검색할 때 쓰는 자격증명입니다.
            `Authorization: Bearer &lt;토큰&gt;` 헤더로 보냅니다. 발급은 로그인 세션에서만 할 수
            있으므로 토큰이 다른 토큰을 만들 수는 없습니다.
          </p>
        </div>

        <form
          className="grid gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6 sm:grid-cols-2"
          onSubmit={(event) => void issue(event)}
        >
          <label className="text-sm text-neutral-400">토큰 이름
            <input
              className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
              onChange={(event) => setName(event.target.value)}
              placeholder="배치 투입 스크립트"
              required
              value={name}
            />
          </label>
          <label className="text-sm text-neutral-400">권한 범위
            <select
              className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
              onChange={(event) => setScope(event.target.value as TokenScope)}
              value={scope}
            >
              <option value="read">읽기 전용 — 검색·조회만</option>
              <option value="read_write">읽기·쓰기 — 문서 등록·수정까지</option>
            </select>
          </label>
          <div className="sm:col-span-2">
            <button
              className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
              disabled={tokenWorking}
              type="submit"
            >
              토큰 발급
            </button>
          </div>
        </form>

        {issued !== null ? (
          <div className="space-y-2 rounded-lg border border-[#0ea5e9] bg-[#141414] p-6" role="status">
            <p className="text-sm text-white">
              &lsquo;{issued.name}&rsquo; 토큰을 발급했습니다. 지금 복사하세요 —{" "}
              <strong className="font-semibold">이 값은 다시 볼 수 없습니다.</strong>
            </p>
            <code className="block break-all rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 font-mono text-sm text-neutral-200">
              {issued.token}
            </code>
            <p className="text-xs text-neutral-500">
              서버에는 해시만 남습니다. 잃어버리면 이 토큰을 폐기하고 새로 발급하세요.
            </p>
          </div>
        ) : null}

        <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-neutral-800 text-neutral-500">
              <tr>
                <th className="px-4 py-3 font-medium">이름</th>
                <th className="px-4 py-3 font-medium">범위</th>
                <th className="px-4 py-3 font-medium">발급일</th>
                <th className="px-4 py-3"><span className="sr-only">작업</span></th>
              </tr>
            </thead>
            <tbody>
              {tokens.length === 0 ? (
                <tr><td className="px-4 py-3 text-neutral-500" colSpan={4}>발급한 토큰이 없습니다.</td></tr>
              ) : tokens.map((token) => (
                <tr className="border-b border-neutral-800 last:border-0" key={token.id}>
                  <td className="px-4 py-3 text-white">{token.name}</td>
                  <td className="px-4 py-3 text-neutral-400">{SCOPE_LABEL[token.scope]}</td>
                  <td className="px-4 py-3 text-neutral-400">{formatDate(token.created_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                      disabled={tokenWorking}
                      onClick={() => void revoke(token)}
                      type="button"
                    >
                      폐기
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tokenError !== null ? <p className="text-sm text-[#ef4444]" role="alert">{tokenError}</p> : null}
      </div>

      <div className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-white">비밀번호</h2>
          <p className="mt-2 text-sm text-neutral-400">
            바꾸면 이 계정으로 열려 있던 모든 기기의 로그인이 끊기고 다시 로그인해야 합니다.
            발급한 API 토큰은 영향을 받지 않습니다.
          </p>
        </div>

        <form
          className="grid gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6 sm:grid-cols-2"
          onSubmit={(event) => void submitPassword(event)}
        >
          <label className="text-sm text-neutral-400">현재 비밀번호
            <input
              className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
              type="password"
              value={currentPassword}
            />
          </label>
          <label className="text-sm text-neutral-400">새 비밀번호
            <input
              className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
              onChange={(event) => setNewPassword(event.target.value)}
              required
              type="password"
              value={newPassword}
            />
          </label>
          <div className="sm:col-span-2">
            <button
              className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
              disabled={passwordWorking}
              type="submit"
            >
              비밀번호 변경
            </button>
          </div>
        </form>
        {passwordError !== null ? <p className="text-sm text-[#ef4444]" role="alert">{passwordError}</p> : null}
      </div>
    </section>
  );
}
