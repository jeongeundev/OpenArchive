"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, createUser, deleteUser, listUsers } from "@/lib/api";
import type { UserSummary } from "@/lib/types";

export default function UsersPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    setUsers(await listUsers());
  }

  useEffect(() => {
    if (!authLoading && auth.is_admin) {
      let active = true;
      listUsers()
        .then((items) => {
          if (active) setUsers(items);
        })
        .catch((reason: unknown) => {
          if (active) {
            setError(reason instanceof ApiError ? reason.detail : "사용자 목록을 불러오지 못했습니다.");
          }
        });
      return () => {
        active = false;
      };
    }
  }, [auth.is_admin, authLoading]);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      await createUser({ username, password, is_admin: isAdmin });
      setUsername("");
      setPassword("");
      setIsAdmin(false);
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "사용자를 생성하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function remove(user: UserSummary): Promise<void> {
    if (!window.confirm(`${user.username} 사용자를 삭제하시겠습니까?`)) return;
    setWorking(true);
    setError(null);
    try {
      await deleteUser(user.id);
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "사용자를 삭제하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-neutral-500">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">사용자 관리</h1>
        <p className="mt-3 text-sm text-neutral-400">계정을 만들고 삭제합니다.</p>
      </div>

      <form className="grid gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6 sm:grid-cols-2" onSubmit={(event) => void submit(event)}>
        <label className="text-sm text-neutral-400">사용자명
          <input className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setUsername(event.target.value)} required value={username} />
        </label>
        <label className="text-sm text-neutral-400">비밀번호
          <input className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
        </label>
        <label className="flex items-center gap-2 text-sm text-neutral-300">
          <input checked={isAdmin} onChange={(event) => setIsAdmin(event.target.checked)} type="checkbox" /> 관리자
        </label>
        <div><button className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400" disabled={working} type="submit">사용자 생성</button></div>
      </form>

      <div className="space-y-3">
        <p className="text-sm text-neutral-500">소유 문서가 있는 사용자는 삭제할 수 없습니다. 문서를 먼저 삭제하세요.</p>
        <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-neutral-800 text-neutral-500"><tr><th className="px-4 py-3 font-medium">사용자명</th><th className="px-4 py-3 font-medium">권한</th><th className="px-4 py-3"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>{users.map((user) => (
              <tr className="border-b border-neutral-800 last:border-0" key={user.id}>
                <td className="px-4 py-3 text-white">{user.username}</td>
                <td className="px-4 py-3 text-neutral-400">{user.is_admin ? "관리자" : "일반 사용자"}</td>
                <td className="px-4 py-3 text-right"><button className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600" disabled={working} onClick={() => void remove(user)} type="button">삭제</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
    </section>
  );
}
