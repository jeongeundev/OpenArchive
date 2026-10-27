// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, createUser, deleteUser, listGroups, listUsers, setUserDepartment } from "@/lib/api";
import type { GroupSummary, UserSummary } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

export default function UsersPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [departmentId, setDepartmentId] = useState("");
  const [groups, setGroups] = useState<GroupSummary[] | null>(null);
  const [working, setWorking] = useState(false);
  const [transferUser, setTransferUser] = useState<string | null>(null);
  const [transferTo, setTransferTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const unmountSignal = useUnmountSignal();

  async function refresh(): Promise<void> {
    setUsers(await listUsers(unmountSignal()));
  }

  useEffect(() => {
    if (!authLoading && auth.is_admin) {
      const controller = new AbortController();
      listUsers(controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) setUsers(items);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason instanceof ApiError ? reason.detail : "사용자 목록을 불러오지 못했습니다.");
          }
        });
      listGroups(controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) setGroups(items);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason instanceof ApiError ? reason.detail : "부서(그룹) 목록을 불러오지 못했습니다.");
          }
        });
      return () => controller.abort();
    }
  }, [auth.is_admin, authLoading]);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      await createUser({ username, password, is_admin: isAdmin, ...(departmentId ? { department_group_id: departmentId } : {}) });
      setUsername("");
      setPassword("");
      setIsAdmin(false);
      setDepartmentId("");
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "사용자를 생성하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function changeDepartment(user: UserSummary, groupId: string): Promise<void> {
    setWorking(true);
    setError(null);
    try {
      await setUserDepartment(user.id, groupId || null);
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "소속 부서를 바꾸지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function remove(user: UserSummary, target?: string): Promise<void> {
    if (!window.confirm(target ? `${user.username} 사용자의 문서와 폴더를 ${target} 사용자에게 이전한 뒤 삭제하시겠습니까? 이전해도 열람 범위는 바뀌지 않습니다.` : `${user.username} 사용자를 삭제하시겠습니까?`)) return;
    setWorking(true);
    setError(null);
    try {
      await deleteUser(user.id, target);
      setTransferUser(null);
      setTransferTo("");
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "사용자를 삭제하지 못했습니다.");
      if (reason instanceof ApiError && reason.status === 409) { setTransferUser(user.id); setTransferTo(""); }
    } finally {
      setWorking(false);
    }
  }

  if (authLoading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-muted">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-strong">사용자 관리</h1>
        <p className="mt-3 text-sm text-muted">계정을 만들고 삭제합니다.</p>
      </div>

      <form className="grid gap-4 rounded-lg border border-line bg-card p-6 sm:grid-cols-2" onSubmit={(event) => void submit(event)}>
        <label className="text-sm text-muted">사용자명
          <input className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-body" onChange={(event) => setUsername(event.target.value)} required value={username} />
        </label>
        <div>
          <label className="text-sm text-muted">비밀번호
            <input aria-describedby="new-user-password-hint" className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-body" minLength={8} onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
          </label>
          <p className="mt-2 text-xs text-muted" id="new-user-password-hint">8자 이상</p>
        </div>
        <div>
          <label className="text-sm text-muted">소속 부서
            <select className="mt-2 w-full rounded-lg border border-line bg-card px-4 py-3 text-body" onChange={(event) => setDepartmentId(event.target.value)} value={departmentId}>
              <option value="">없음</option>
              {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
            </select>
          </label>
          {groups?.length === 0 ? <p className="mt-2 text-xs text-muted">먼저 그룹 관리에서 부서(그룹)를 만드세요. <Link className="underline hover:text-strong" href="/admin/groups">그룹 관리</Link></p> : null}
        </div>
        <label className="flex items-center gap-2 text-sm text-body">
          <input checked={isAdmin} onChange={(event) => setIsAdmin(event.target.checked)} type="checkbox" /> 관리자
        </label>
        <div><button className="rounded-lg bg-strong px-4 py-2 text-sm text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint" disabled={working} type="submit">사용자 생성</button></div>
      </form>

      <div className="space-y-3">
        <p className="text-sm text-muted">문서나 폴더를 소유한 사용자는 이전받을 사용자를 고른 뒤 삭제합니다. 이전해도 열람 범위는 바뀌지 않습니다.</p>
        <div className="overflow-x-auto rounded-lg border border-line bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-muted"><tr><th className="px-4 py-3 font-medium">사용자명</th><th className="px-4 py-3 font-medium">소속 부서</th><th className="px-4 py-3 font-medium">권한</th><th className="px-4 py-3"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>{users.map((user) => (
              <tr className="border-b border-line last:border-0" key={user.id}>
                <td className="px-4 py-3 text-strong">{user.username}</td>
                <td className="px-4 py-3">
                  <select aria-label={`${user.username} 소속 부서`} className="rounded-lg border border-line bg-card px-3 py-2 text-body" disabled={working} onChange={(event) => void changeDepartment(user, event.target.value)} value={user.department?.id ?? ""}>
                    <option value="">없음</option>
                    {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3 text-muted">{user.is_admin ? "관리자" : "일반 사용자"}</td>
                <td className="px-4 py-3 text-right"><button className="text-muted hover:text-strong disabled:text-faint" disabled={working || user.username === auth.username} onClick={() => void remove(user)} title={user.username === auth.username ? "자기 계정은 삭제할 수 없습니다." : undefined} type="button">삭제</button>
                  {transferUser === user.id ? <div className="mt-3 space-y-3 text-left">
                    <label className="block text-muted">이전받을 사용자
                      <select className="mt-2 block rounded-lg border border-line bg-card px-4 py-3 text-body" disabled={working} value={transferTo} onChange={event => setTransferTo(event.target.value)}>
                        <option value="">사용자를 고르세요</option>
                        {users.filter(item => item.id !== user.id && item.username !== auth.username).map(item => <option key={item.id} value={item.username}>{item.username}</option>)}
                      </select>
                    </label>
                    <button className="rounded-lg bg-strong px-4 py-2 text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint" disabled={working || !transferTo} onClick={() => void remove(user, transferTo)} type="button">이전 후 삭제</button>
                  </div> : null}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
    </section>
  );
}
