"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import {
  ApiError,
  addGroupMember,
  createGroup,
  deleteGroup,
  listGroups,
  listUsers,
  removeGroupMember,
} from "@/lib/api";
import type { GroupSummary, UserSummary } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

export default function GroupsPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unmountSignal = useUnmountSignal();

  async function refresh(): Promise<void> {
    setGroups(await listGroups(unmountSignal()));
  }

  useEffect(() => {
    if (!authLoading && auth.is_admin) {
      const controller = new AbortController();
      Promise.all([listGroups(controller.signal), listUsers(controller.signal)])
        .then(([groupItems, userItems]) => {
          if (controller.signal.aborted) return;
          setGroups(groupItems);
          setUsers(userItems);
          setLoaded(true);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason instanceof ApiError ? reason.detail : "그룹 목록을 불러오지 못했습니다.");
          }
        });
      return () => controller.abort();
    }
  }, [auth.is_admin, authLoading]);

  async function run(action: () => Promise<unknown>, fallback: string): Promise<void> {
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : fallback);
    } finally {
      setWorking(false);
    }
  }

  function submit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void run(async () => {
      await createGroup(name.trim());
      setName("");
    }, "그룹을 생성하지 못했습니다.");
  }

  function addMember(group: GroupSummary): void {
    const username = selected[group.id];
    if (!username) return;
    void run(async () => {
      await addGroupMember(group.id, username);
      setSelected((current) => ({ ...current, [group.id]: "" }));
    }, "구성원을 추가하지 못했습니다.");
  }

  function remove(group: GroupSummary): void {
    if (!window.confirm(
      `${group.name} 그룹을 삭제하시겠습니까? 이 그룹에 부여된 문서는 구성원에게 더 이상 보이지 않습니다.`,
    )) return;
    void run(() => deleteGroup(group.id), "그룹을 삭제하지 못했습니다.");
  }

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-neutral-500">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">그룹 관리</h1>
        <p className="mt-3 text-sm text-neutral-400">
          그룹 구성원을 바꾸면 그 그룹에 부여된 문서의 열람이 바뀝니다. 관리자도 보면 안 되는 문서는 사용자에게 직접 부여하세요.
        </p>
      </div>

      <form className="flex flex-wrap items-end gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6" onSubmit={submit}>
        <label className="text-sm text-neutral-400">그룹 이름
          <input className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setName(event.target.value)} required value={name} />
        </label>
        <button className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400" disabled={working} type="submit">그룹 생성</button>
      </form>

      <p className="text-sm text-neutral-500">그룹 이름은 문서를 부여할 때 쓰는 이름이라 바꿀 수 없습니다.</p>

      {loaded && groups.length === 0 ? <p className="text-sm text-neutral-500">아직 그룹이 없습니다.</p> : null}

      <div className="space-y-3">
        {groups.map((group) => {
          const candidates = users.filter((user) => !group.members.includes(user.username));
          const headingId = `group-${group.id}`;
          return (
            <section aria-labelledby={headingId} className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6" key={group.id}>
              <div className="flex items-center justify-between gap-4">
                <h2 className="text-sm font-medium text-white" id={headingId}>{group.name}</h2>
                <button className="text-sm text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600" disabled={working} onClick={() => remove(group)} type="button">그룹 삭제</button>
              </div>
              {group.members.length === 0 ? (
                <p className="text-sm text-neutral-500">구성원이 없습니다.</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {group.members.map((member) => (
                    <li className="flex items-center justify-between gap-4" key={member}>
                      <span className="text-neutral-300">{member}</span>
                      <button aria-label={`${member} 제거`} className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600" disabled={working} onClick={() => void run(() => removeGroupMember(group.id, member), "구성원을 제거하지 못했습니다.")} type="button">제거</button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap items-end gap-3">
                <label className="text-sm text-neutral-400">추가할 사용자
                  <select className="mt-2 block rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setSelected((current) => ({ ...current, [group.id]: event.target.value }))} value={selected[group.id] ?? ""}>
                    <option value="">선택</option>
                    {candidates.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
                  </select>
                </label>
                <button className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400" disabled={working || !selected[group.id]} onClick={() => addMember(group)} type="button">구성원 추가</button>
              </div>
            </section>
          );
        })}
      </div>
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
    </section>
  );
}
