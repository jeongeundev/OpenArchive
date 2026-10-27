// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import { GranteePicker } from "./GranteePicker";
import { ApiError, getFolderAccess, listPrincipals, setFolderAccess, setFolderDepartment } from "@/lib/api";
import { VISIBILITY_LABEL, type Folder, type FolderScope, type Principals, type Visibility } from "@/lib/types";

/**
 * 최상위 폴더의 열람 범위. 만든 사람이 아니어도 보이고 저장할 수 있다 — 거부는 서버가 보인다(B10).
 * 범위 변경은 세션 전용이라 화면의 쿠키 세션으로만 부른다(ADR-054 결정 4).
 */
export function FolderAccessPanel({ folder, onSaved }: {
  folder: Folder;
  onSaved: () => void;
}): React.ReactElement | null {
  const isRoot = folder.parent_id === null;
  const [principals, setPrincipals] = useState<Principals>({ users: [], groups: [] });
  const [visibility, setVisibility] = useState<Visibility>(folder.scope.visibility);
  const [users, setUsers] = useState<string[]>(folder.scope.users);
  const [groups, setGroups] = useState<string[]>(folder.scope.groups);
  const [loaded, setLoaded] = useState(!folder.can_change_access);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isRoot) return;
    const controller = new AbortController();
    void listPrincipals(controller.signal)
      .then(directory => { if (!controller.signal.aborted) setPrincipals(directory); })
      .catch(() => undefined);
    // 범위 조회는 만든 사람만 된다 — 그 밖의 사람은 폴더 목록의 scope로 채운다.
    if (folder.can_change_access) {
      void getFolderAccess(folder.id, controller.signal)
        .then(access => {
          if (controller.signal.aborted) return;
          setVisibility(access.visibility);
          setUsers(access.users);
          setGroups(access.groups);
          setLoaded(true);
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          setError(reason instanceof ApiError ? reason.detail : "열람 범위를 불러오지 못했습니다.");
          setLoaded(true);
        });
    }
    return () => controller.abort();
  }, [folder.id, folder.can_change_access, isRoot]);

  if (!isRoot) return null;

  async function save(): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    const next: FolderScope = visibility === "public"
      ? { visibility, users: [], groups: [] }
      : { visibility, users, groups };
    try {
      const saved = await setFolderAccess(folder.id, next);
      setUsers(saved.users);
      setGroups(saved.groups);
      setMessage("열람 범위를 저장했습니다.");
      onSaved();
    } catch (reason: unknown) {
      // 실패해도 고른 값은 지우지 않는다.
      setError(reason instanceof ApiError ? reason.detail : "열람 범위를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  const disabled = saving || !loaded;

  return (
    <section className="space-y-4 border-t border-line pt-4">
      <h3 className="text-sm font-medium text-muted">폴더 열람 범위</h3>
      {!folder.can_change_access ? (
        <p className="text-sm text-muted">폴더를 만든 사람만 바꿀 수 있습니다</p>
      ) : null}
      <fieldset disabled={disabled}>
        <legend className="sr-only">폴더 열람 범위</legend>
        <div className="flex gap-4 text-sm text-body">
          {(["public", "private"] as const).map(value => (
            <label className="flex items-center gap-2" key={value}>
              <input checked={visibility === value} name={`folder-visibility-${folder.id}`}
                onChange={() => setVisibility(value)} type="radio" value={value} />
              {VISIBILITY_LABEL[value]}
            </label>
          ))}
        </div>
      </fieldset>
      {visibility === "private" ? (
        <>
          <GranteePicker disabled={disabled} groups={groups} principals={principals} users={users}
            description="선택한 사용자와 그룹 구성원만 이 폴더와 폴더 범위를 따르는 문서를 봅니다."
            onChange={next => { setUsers(next.users); setGroups(next.groups); }} />
          {users.length === 0 && groups.length === 0 ? (
            <p className="text-sm text-muted">대상을 고르지 않으면 폴더를 만든 사람만 봅니다</p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-muted">조직 공개 폴더는 로그인한 모든 사용자가 봅니다.</p>
      )}
      <p className="text-sm text-muted">폴더 범위를 따르는 문서(다른 사람이 넣은 문서 포함)에 바로 적용됩니다</p>
      <button type="button" disabled={disabled} onClick={() => void save()}
        className="rounded-lg bg-strong px-4 py-2 text-sm text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint">
        {saving ? "저장 중…" : "열람 범위 저장"}
      </button>
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
      {message !== null ? <p className="text-sm text-muted">{message}</p> : null}
      {folder.can_change_access ? (
        <FolderDepartmentSelect folder={folder} groups={principals.groups} onSaved={onSaved} />
      ) : null}
    </section>
  );
}

/**
 * 부서 폴더 지정(D15). 만든 사람에게만 그린다. 고를 수 있는 그룹을 따로 묻는 API가 없어 전체 그룹을 보이고,
 * 구성원이 아니거나 열람 범위가 부서에 닿지 않으면 서버 거부 문구를 그대로 보인다.
 */
function FolderDepartmentSelect({ folder, groups, onSaved }: {
  folder: Folder;
  groups: string[];
  onSaved: () => void;
}): React.ReactElement {
  const [group, setGroup] = useState(folder.department?.name ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const options = group !== "" && !groups.includes(group) ? [group, ...groups] : groups;

  async function save(): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await setFolderDepartment(folder.id, group === "" ? null : group);
      setMessage("부서 폴더를 저장했습니다.");
      onSaved();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "부서 폴더를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 border-t border-line pt-4">
      <label className="flex flex-wrap items-center gap-2 text-sm text-body">
        <span className="font-medium text-muted">부서 폴더</span>
        <select value={group} disabled={saving} onChange={event => setGroup(event.target.value)}
          className="rounded-lg border border-line bg-card px-2 py-1 text-sm text-body">
          <option value="">지정 안 함</option>
          {options.map(name => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      <p className="text-sm text-muted">그 부서 구성원이 볼 수 있는 최상위 폴더만 부서 폴더가 됩니다. 부서마다 하나입니다.</p>
      <button type="button" disabled={saving} onClick={() => void save()}
        className="rounded-lg border border-line px-4 py-2 text-sm text-body hover:bg-sunken disabled:text-faint">
        {saving ? "저장 중…" : "부서 폴더 저장"}
      </button>
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
      {message !== null ? <p className="text-sm text-muted">{message}</p> : null}
    </div>
  );
}
