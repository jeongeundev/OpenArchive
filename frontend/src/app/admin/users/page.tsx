// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ApiError, createUser, deleteUser, listGroups, listUsers, setUserDepartment } from "@/lib/api";
import type { GroupSummary, UserSummary } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

function Icon({ path }: { path: string }): React.ReactElement {
  return <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;
}

const ROLE_OPTIONS = [
  { admin: false, title: "일반 사용자", description: "문서 업로드·검색" },
  { admin: true, title: "관리자", description: "계정·부서·시스템 관리" },
] as const;

export default function UsersPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [departmentId, setDepartmentId] = useState("");
  const [groups, setGroups] = useState<GroupSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<"all" | "admin" | "member">("all");
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
        .then((items) => { if (!controller.signal.aborted) setUsers(items); })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.detail : "사용자 목록을 불러오지 못했습니다."); });
      listGroups(controller.signal)
        .then((items) => { if (!controller.signal.aborted) setGroups(items); })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.detail : "부서(그룹) 목록을 불러오지 못했습니다."); });
      return () => controller.abort();
    }
  }, [auth.is_admin, authLoading]);

  function resetForm(): void {
    setUsername("");
    setPassword("");
    setIsAdmin(false);
    setDepartmentId("");
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      await createUser({ username, password, is_admin: isAdmin, ...(departmentId ? { department_group_id: departmentId } : {}) });
      resetForm();
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
      if (reason instanceof ApiError && reason.status === 409) {
        setTransferUser(user.id);
        setTransferTo("");
      }
    } finally {
      setWorking(false);
    }
  }

  // 소유물이 보이면 확인창 없이 이전 선택부터 연다 — 목록이 낡았으면 서버의 409가 같은 자리로 보낸다.
  function startDelete(user: UserSummary): void {
    if ((user.owned_documents ?? 0) + (user.created_folders ?? 0) > 0) {
      setTransferUser(user.id);
      setTransferTo("");
      return;
    }
    void remove(user);
  }

  if (authLoading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-muted">관리자 권한이 필요합니다.</p>;

  const adminCount = users.filter(user => user.is_admin).length;
  const counts = { all: users.length, admin: adminCount, member: users.length - adminCount };
  const needle = query.trim().toLowerCase();
  const visible = users.filter(user => (role === "all" || user.is_admin === (role === "admin")) && user.username.toLowerCase().includes(needle));
  const fieldClass = "mt-2 block w-full rounded-lg border border-line-strong bg-card px-3 py-2 text-sm text-body";

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-sm font-semibold text-accent">관리</p>
          <h1 className="text-4xl font-semibold text-strong">사용자 관리</h1>
          <p className="mt-3 text-sm text-muted">계정을 만들고, 소속 부서와 권한을 관리합니다.</p>
        </div>
        <button aria-expanded={formOpen} className="rounded-lg bg-strong px-4 py-3 text-sm font-medium text-on-strong hover:bg-strong/90" onClick={() => setFormOpen(true)} type="button">+ 사용자 추가</button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {([["전체 계정", counts.all], ["관리자", counts.admin], ["일반 사용자", counts.member], ["부서 미지정", users.filter(user => !user.department).length]] as const).map(([label, count]) => <div key={label} className="rounded-xl border border-line bg-card px-5 py-4"><p className="text-xs text-muted">{label}</p><p className="mt-2 text-3xl font-semibold text-strong">{count}</p></div>)}
      </div>

      {formOpen ? <form className="space-y-4 rounded-xl bg-accent-soft p-6" onSubmit={(event) => void submit(event)}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-semibold text-strong">새 사용자</h2>
          <p className="text-xs text-muted">계정을 만들면 감사 로그에 기록됩니다</p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <label className="text-xs font-medium text-muted">사용자명
            <input className={fieldClass} onChange={(event) => setUsername(event.target.value)} placeholder="예) 홍길동" required value={username} />
          </label>
          <div>
            <label className="text-xs font-medium text-muted">초기 비밀번호
              <input aria-describedby="new-user-password-hint" className={fieldClass} minLength={8} onChange={(event) => setPassword(event.target.value)} placeholder="8자 이상" required type="password" value={password} />
            </label>
            <span className="sr-only" id="new-user-password-hint">8자 이상</span>
          </div>
          <div>
            <label className="text-xs font-medium text-muted">소속 부서
              <select className={fieldClass} onChange={(event) => setDepartmentId(event.target.value)} value={departmentId}>
                <option value="">없음</option>
                {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
              </select>
            </label>
            {groups?.length === 0 ? <p className="mt-2 text-xs text-muted">먼저 그룹 관리에서 부서(그룹)를 만드세요. <Link className="underline hover:text-strong" href="/admin/groups">그룹 관리</Link></p> : null}
          </div>
        </div>
        <fieldset>
          <legend className="text-xs font-medium text-muted">권한</legend>
          <div className="mt-2 grid gap-2">
            {ROLE_OPTIONS.map(option => <label key={option.title} className={`flex cursor-pointer items-center gap-3 rounded-lg border bg-card px-4 py-3 ${isAdmin === option.admin ? "border-accent" : "border-line"}`}>
              <input checked={isAdmin === option.admin} name="new-user-role" onChange={() => setIsAdmin(option.admin)} type="radio" />
              <span><span className="block text-sm font-medium text-strong">{option.title}</span><span className="block text-xs text-muted">{option.description}</span></span>
            </label>)}
          </div>
        </fieldset>
        <div className="flex justify-end gap-2">
          <button className="rounded-lg border border-line-strong bg-card px-4 py-2 text-sm text-body" onClick={() => { setFormOpen(false); resetForm(); }} type="button">취소</button>
          <button className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-strong hover:bg-accent/90 disabled:bg-sunken disabled:text-faint" disabled={working} type="submit">사용자 생성</button>
        </div>
      </form> : null}

      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex min-w-48 flex-1 items-center gap-2 rounded-lg border border-line-strong bg-card px-3 py-2 text-muted">
          <Icon path="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z M20 20l-4-4" />
          <input aria-label="이름으로 찾기" className="w-full bg-transparent text-sm text-body outline-none" onChange={(event) => setQuery(event.target.value)} placeholder="이름으로 찾기" type="search" value={query} />
        </label>
        <div aria-label="권한 필터" className="flex flex-wrap gap-2">
          {([["all", "전체"], ["admin", "관리자"], ["member", "일반 사용자"]] as const).map(([value, label]) => (
            <button key={value} type="button" aria-pressed={role === value} onClick={() => setRole(value)}
              className={`rounded-full border px-4 py-2 text-sm font-medium ${role === value ? "border-strong bg-strong text-on-strong" : "border-line-strong text-body"}`}>
              {label} {counts[value]}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-line bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-sunken text-muted"><tr>
            <th className="px-5 py-3 font-medium">사용자</th>
            <th className="px-5 py-3 font-medium">소속 부서</th>
            <th className="px-5 py-3 font-medium">권한</th>
            <th className="px-5 py-3 font-medium">소유 문서</th>
            <th className="px-5 py-3 text-right font-medium">작업</th>
          </tr></thead>
          <tbody>{visible.map((user) => {
            const self = user.username === auth.username;
            return [
              <tr className="border-b border-line last:border-0" key={user.id}>
                <td className="px-5 py-4"><div className="flex items-center gap-2">
                  <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{Array.from(user.username)[0]}</span>
                  <span className="font-medium text-strong">{user.username}</span>
                  {self ? <span className="rounded bg-sunken px-1.5 py-0.5 text-xs text-muted">나</span> : null}
                </div></td>
                <td className="px-5 py-4">
                  <select aria-label={`${user.username} 소속 부서`} className="rounded-lg border border-line-strong bg-card px-3 py-2 text-body" disabled={working} onChange={(event) => void changeDepartment(user, event.target.value)} value={user.department?.id ?? ""}>
                    <option value="">없음</option>
                    {groups?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                  </select>
                </td>
                <td className="px-5 py-4"><div className="flex flex-wrap gap-1">
                  {user.is_admin || !user.managed_departments?.length ? <span className={`rounded px-2 py-1 text-xs font-medium ${user.is_admin ? "bg-accent-soft text-accent" : "bg-sunken text-body"}`}>{user.is_admin ? "관리자" : "일반 사용자"}</span> : null}
                  {user.managed_departments?.length ? <span className="rounded border border-accent px-2 py-0.5 text-xs font-medium text-accent" title={`${user.managed_departments.join(", ")} 관리`}>부서 관리자</span> : null}
                </div></td>
                <td className="px-5 py-4 text-body"><span>{user.owned_documents ?? 0}건</span>{(user.created_folders ?? 0) > 0 ? <span className="ml-1 text-muted">· 폴더 {user.created_folders}</span> : null}</td>
                <td className="px-5 py-4 text-right">{self
                  ? <span className="text-xs text-faint">본인 계정은 삭제할 수 없음</span>
                  : <button className="rounded-lg border border-failed px-3 py-1.5 text-sm font-medium text-failed hover:bg-failed-soft disabled:border-line disabled:text-faint" disabled={working} onClick={() => startDelete(user)} type="button">삭제</button>}
                </td>
              </tr>,
              transferUser === user.id ? <tr className="border-b border-line bg-page last:border-0" key={`${user.id}-transfer`}>
                <td className="px-5 py-4" colSpan={5}><div className="flex flex-wrap items-end gap-3">
                  <label className="text-xs font-medium text-muted">이전받을 사용자
                    <select className="mt-2 block rounded-lg border border-line-strong bg-card px-3 py-2 text-sm text-body" disabled={working} value={transferTo} onChange={event => setTransferTo(event.target.value)}>
                      <option value="">사용자를 고르세요</option>
                      {users.filter(item => item.id !== user.id && item.username !== auth.username).map(item => <option key={item.id} value={item.username}>{item.username}</option>)}
                    </select>
                  </label>
                  <button className="rounded-lg bg-strong px-4 py-2 text-sm text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint" disabled={working || !transferTo} onClick={() => void remove(user, transferTo)} type="button">이전 후 삭제</button>
                  <button className="rounded-lg border border-line-strong px-4 py-2 text-sm text-body" onClick={() => { setTransferUser(null); setTransferTo(""); }} type="button">이전 취소</button>
                  <p className="basis-full text-xs text-muted">{user.username} 사용자의 문서와 폴더를 옮긴 뒤 계정을 삭제합니다. 이전해도 열람 범위는 바뀌지 않습니다.</p>
                </div></td>
              </tr> : null,
            ];
          })}</tbody>
        </table>
      </div>

      <div className="flex items-center gap-3 rounded-xl bg-sunken px-4 py-4 text-sm text-body">
        <Icon path="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z M12 11v5 M12 8h.01" />
        <p>문서나 폴더를 소유한 사용자는 이전받을 사용자를 고른 뒤 삭제합니다. 소속 부서를 바꾸면 그 부서 폴더의 열람 범위가 바로 적용됩니다.</p>
      </div>
    </section>
  );
}
