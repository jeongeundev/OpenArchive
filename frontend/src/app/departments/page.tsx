// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { FolderNameForm } from "@/components/FolderTree";
import {
  addDepartmentMember, ApiError, createFolder, createGroup, listDepartments, listPrincipals,
  removeDepartmentMember, setDepartmentManager, setFolderAccess, setFolderDepartment, updateDepartment,
} from "@/lib/api";
import { folderChildren, scopeLabel } from "@/lib/folders";
import { useFolders } from "@/lib/useFolders";
import type { Department, Folder } from "@/lib/types";

/** 카드 하나 — 부서(폴더가 없을 수 있다) 또는 부서가 아닌 최상위 폴더. */
interface Card {
  key: string;
  name: string;
  department: Department | null;
  folder: Folder | null;
}

const FOLDER_ICON = "M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z";

function message(reason: unknown, fallback: string): string {
  return reason instanceof ApiError ? reason.detail : fallback;
}

/**
 * 부서 — 부서(그룹)와 부서가 아닌 최상위 폴더를 카드로 보이고, 고른 카드의 폴더 구조·구성원·열람 권한을 보인다.
 * 폴더 목록과 부서의 `folder_id`·수는 이미 열람 술어를 거친 것이라, 보이는 폴더만 센다.
 * 구성원 변경은 부서 API로만 한다 — 부서 관리자는 관리자 그룹 API에 접근할 수 없다 (ADR-066).
 */
export default function DepartmentsPage(): React.ReactElement {
  const { auth } = useAuth();
  const { folders, loading, error, refresh } = useFolders(true);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [departmentsError, setDepartmentsError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [addingDepartment, setAddingDepartment] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    listDepartments(controller.signal).then(list => {
      if (controller.signal.aborted) return;
      setDepartments(list);
      setDepartmentsError(null);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setDepartmentsError(message(reason, "부서를 불러오지 못했습니다."));
    }).finally(() => {
      if (!controller.signal.aborted) setDepartmentsLoading(false);
    });
    return () => controller.abort();
  }, [version]);

  function reload(): void {
    setVersion(value => value + 1);
    refresh();
  }

  const mine = auth.department?.name ?? null;
  const cards: Card[] = [
    ...departments.map(department => ({
      key: `department:${department.id}`, name: department.name, department,
      folder: folders.find(folder => folder.id === department.folder_id) ?? null,
    })),
    ...folders.filter(folder => folder.parent_id === null && !folder.department)
      .map(folder => ({ key: `folder:${folder.id}`, name: folder.name, department: null, folder })),
  ].sort((a, b) => Number(b.department?.name === mine) - Number(a.department?.name === mine)
    || a.name.localeCompare(b.name, "ko"));

  const descendants = (folder: Folder): Folder[] =>
    folderChildren(folders, folder.id).flatMap(child => [child, ...descendants(child)]);
  const documents = (folder: Folder): number =>
    folder.document_count + descendants(folder).reduce((sum, child) => sum + child.document_count, 0);

  const selected = cards.find(card => card.key === picked)
    ?? cards.find(card => card.department !== null && card.department.name === mine) ?? cards[0] ?? null;

  const shownError = error ?? departmentsError;

  return (
    <section className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-4xl font-semibold text-strong">부서</h1>
          <p className="mt-3 text-sm text-muted">부서별 폴더, 구성원, 기본 열람 범위를 관리합니다.</p>
        </div>
        {auth.is_admin && (
          <div className="flex shrink-0 items-start gap-3">
            <Link href="/admin/groups" className="rounded-md border border-line-strong bg-card px-4 py-2 text-sm font-medium text-strong">
              그룹 관리
            </Link>
            {addingDepartment ? (
              <div className="w-64">
                <FolderNameForm label="부서 이름" submitLabel="추가" onCancel={() => setAddingDepartment(false)}
                  onSubmit={async name => {
                    await createGroup(name);
                    setAddingDepartment(false);
                    reload();
                  }} />
              </div>
            ) : (
              <button type="button" onClick={() => setAddingDepartment(true)}
                className="rounded-md bg-strong px-4 py-2 text-sm font-medium text-on-strong">
                부서 추가
              </button>
            )}
          </div>
        )}
      </div>

      {shownError !== null ? <p className="text-sm text-failed">{shownError}</p>
        : loading || departmentsLoading ? <p className="text-sm text-muted">불러오는 중…</p>
        : selected === null ? <p className="text-sm text-muted">볼 수 있는 부서 폴더가 없습니다.</p>
        : (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {cards.map(card => (
                <button key={card.key} type="button" onClick={() => setPicked(card.key)} aria-pressed={card.key === selected.key}
                  className={`rounded-xl border p-5 text-left ${card.key === selected.key ? "border-accent bg-accent-soft" : "border-line bg-card"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-semibold text-strong">{card.name}</span>
                    <span className="shrink-0 rounded-full bg-sunken px-2 py-0.5 text-xs text-muted">
                      {card.department === null ? "공용 폴더" : card.folder === null ? "폴더 없음" : "부서 폴더"}
                    </span>
                  </div>
                  <p data-testid="department-counts" className="mt-3 text-sm text-muted">
                    문서 <strong className="text-strong">{card.folder ? documents(card.folder) : 0}</strong>{" "}
                    폴더 <strong className="text-strong">{card.folder ? descendants(card.folder).length : 0}</strong>
                    {card.department !== null && <>{" "}구성원 <strong className="text-strong">{card.department.members.length}</strong></>}
                  </p>
                </button>
              ))}
            </div>
            <Detail key={selected.key} card={selected} folders={folders} documents={documents}
              username={auth.username} onChanged={reload} />
          </>
        )}
    </section>
  );
}

function Detail({ card, folders, documents, username, onChanged }: {
  card: Card;
  folders: Folder[];
  documents: (folder: Folder) => number;
  username: string | null;
  onChanged: () => void;
}): React.ReactElement {
  const router = useRouter();
  const { department, folder } = card;
  const canManage = department?.can_manage ?? false;
  const isMember = department?.members.some(member => member.username === username) ?? false;
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(department?.description ?? "");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [addingMember, setAddingMember] = useState(false);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [candidate, setCandidate] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, fallback: string): Promise<boolean> {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (reason: unknown) {
      setActionError(message(reason, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveDescription(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (department === null) return;
    if (await run(() => updateDepartment(department.id, description.trim()), "설명을 저장하지 못했습니다.")) setEditing(false);
  }

  async function createDepartmentFolder(): Promise<void> {
    if (department === null) return;
    // 순서가 중요하다 — 제한 폴더를 부서 폴더로 지정하려면 그 그룹이 범위에 있어야 한다. 중간에 실패하면 만든 폴더는 남긴다.
    await run(async () => {
      try {
        const created = await createFolder({ name: department.name });
        await setFolderAccess(created.id, { visibility: "private", users: [], groups: [department.name] });
        await setFolderDepartment(created.id, department.name);
      } catch (reason: unknown) {
        onChanged();
        throw reason;
      }
    }, "부서 폴더를 만들지 못했습니다.");
  }

  function openMemberForm(): void {
    if (department === null) return;
    setAddingMember(true);
    setActionError(null);
    const members = new Set(department.members.map(member => member.username));
    listPrincipals().then(principals => setCandidates(principals.users.filter(user => !members.has(user))))
      .catch((reason: unknown) => setActionError(message(reason, "사용자 목록을 불러오지 못했습니다.")));
  }

  async function addMember(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (department === null || !candidate) return;
    if (await run(() => addDepartmentMember(department.id, candidate), "구성원을 추가하지 못했습니다.")) {
      setAddingMember(false);
      setCandidate("");
    }
  }

  function removeMember(target: string): void {
    if (department === null || !window.confirm(`「${target}」을(를) ${department.name} 구성원에서 빼시겠습니까?`)) return;
    void run(() => removeDepartmentMember(department.id, target), "구성원을 빼지 못했습니다.");
  }

  function tree(parent: Folder): React.ReactNode[] {
    return folderChildren(folders, parent.id).map(child => {
      const children = tree(child);
      return (
        <li key={child.id}>
          <button type="button" onClick={() => router.push(`/documents?folder=${encodeURIComponent(child.id)}`)}
            className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm text-body hover:bg-card">
            <svg aria-hidden="true" className="size-4 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d={FOLDER_ICON} /></svg>
            <span className="min-w-0 flex-1 truncate">{child.name}</span>
            <span className="text-xs text-muted">{documents(child)}</span>
          </button>
          {children.length > 0 ? <ul className="ml-3 border-l border-line pl-2">{children}</ul> : null}
        </li>
      );
    });
  }

  const subfolders = folder === null ? [] : tree(folder);
  const accentButton = "text-sm font-medium text-accent hover:underline disabled:opacity-50";

  return (
    <div className="rounded-xl border border-line bg-card p-8">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-strong">{card.name}</h2>
          {editing ? (
            <form onSubmit={saveDescription} className="mt-2 flex flex-wrap items-center gap-2">
              <input aria-label="부서 설명" value={description} maxLength={200} autoFocus
                onChange={event => setDescription(event.target.value)}
                className="w-96 max-w-full rounded-lg border border-line bg-card px-3 py-1.5 text-sm text-body" />
              <button type="submit" disabled={busy}
                className="rounded-lg bg-strong px-3 py-1 text-sm text-on-strong disabled:opacity-50">저장</button>
              <button type="button" onClick={() => { setEditing(false); setDescription(department?.description ?? ""); }}
                className="text-sm text-muted hover:text-strong">취소</button>
            </form>
          ) : department?.description ? <p className="mt-2 text-sm text-body">{department.description}</p>
            : canManage ? <p className="mt-2 text-sm text-muted">설명이 없습니다.</p> : null}
        </div>
        <div className="flex shrink-0 gap-2">
          {folder !== null && (
            <Link href={`/documents?folder=${encodeURIComponent(folder.id)}`}
              className="rounded-md bg-strong px-4 py-2 text-sm font-medium text-on-strong">
              문서 보기
            </Link>
          )}
          {canManage && !editing && (
            <button type="button" onClick={() => setEditing(true)}
              className="rounded-md border border-line-strong bg-card px-4 py-2 text-sm font-medium text-strong">
              설정
            </button>
          )}
        </div>
      </div>
      {actionError !== null ? <p role="alert" className="mt-4 text-sm text-failed">{actionError}</p> : null}

      <div className="mt-8 grid gap-8 lg:grid-cols-3">
        <section aria-labelledby="department-tree">
          <h3 id="department-tree" className="text-sm font-semibold text-strong">폴더 구조</h3>
          <div className="mt-3 rounded-lg bg-sunken p-2">
            {subfolders.length ? <ul>{subfolders}</ul> : <p className="px-3 py-1.5 text-sm text-muted">하위 폴더가 없습니다.</p>}
          </div>
          <div className="mt-3">
            {folder !== null ? (
              creatingFolder ? (
                <FolderNameForm label="새 폴더 이름" submitLabel="만들기" onCancel={() => setCreatingFolder(false)}
                  onSubmit={async name => {
                    await createFolder({ name, parentId: folder.id });
                    setCreatingFolder(false);
                    onChanged();
                  }} />
              ) : <button type="button" onClick={() => setCreatingFolder(true)} className={accentButton}>+ 새 폴더</button>
            ) : canManage && isMember ? (
              <button type="button" onClick={() => void createDepartmentFolder()} disabled={busy} className={accentButton}>
                + 부서 폴더 만들기
              </button>
            ) : null}
          </div>
        </section>

        {department !== null ? (
          <section aria-labelledby="department-members">
            <h3 id="department-members" className="text-sm font-semibold text-strong">구성원</h3>
            {department.members.length ? (
              <ul className="mt-3 space-y-3">
                {department.members.map(member => (
                  <li key={member.username} className="flex items-center gap-3">
                    <span aria-hidden="true" className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
                      {member.username.slice(0, 1)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-strong">{member.username}</p>
                      <p className="text-xs text-muted">{member.manager ? "부서 관리자" : "구성원"}</p>
                    </div>
                    {canManage && (
                      <div className="flex shrink-0 gap-3 text-xs">
                        <button type="button" disabled={busy} aria-label={`${member.username} 관리자 ${member.manager ? "해제" : "지정"}`}
                          onClick={() => void run(() => setDepartmentManager(department.id, member.username, !member.manager), "관리자를 바꾸지 못했습니다.")}
                          className="text-muted hover:text-strong disabled:opacity-50">
                          {member.manager ? "관리자 해제" : "관리자 지정"}
                        </button>
                        <button type="button" disabled={busy} aria-label={`${member.username} 빼기`}
                          onClick={() => removeMember(member.username)}
                          className="text-muted hover:text-failed disabled:opacity-50">
                          빼기
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : <p className="mt-3 text-sm text-muted">구성원이 없습니다.</p>}
            {canManage && (
              <div className="mt-4">
                {addingMember ? (
                  <form onSubmit={addMember} className="flex flex-wrap items-center gap-2">
                    <select aria-label="추가할 사용자" value={candidate} onChange={event => setCandidate(event.target.value)}
                      className="rounded-lg border border-line bg-card px-3 py-1.5 text-sm text-body">
                      <option value="">사용자 선택</option>
                      {candidates.map(user => <option key={user} value={user}>{user}</option>)}
                    </select>
                    <button type="submit" disabled={busy || !candidate}
                      className="rounded-lg bg-strong px-3 py-1 text-sm text-on-strong disabled:opacity-50">추가</button>
                    <button type="button" onClick={() => setAddingMember(false)} className="text-sm text-muted hover:text-strong">취소</button>
                  </form>
                ) : <button type="button" onClick={openMemberForm} className={accentButton}>+ 구성원 추가</button>}
              </div>
            )}
          </section>
        ) : (
          <section aria-labelledby="department-audience">
            <h3 id="department-audience" className="text-sm font-semibold text-strong">열람 대상</h3>
            <p className="mt-3 text-sm text-body">
              {folder?.scope.visibility === "public"
                ? "조직 공개 폴더 — 로그인한 모든 사용자가 봅니다."
                : "제한 폴더 — 부여된 사용자·그룹만 봅니다."}
            </p>
          </section>
        )}

        <section aria-labelledby="department-access">
          <h3 id="department-access" className="text-sm font-semibold text-strong">열람 권한</h3>
          <dl className="mt-3 divide-y divide-line text-sm">
            <div className="flex justify-between gap-3 py-2.5"><dt className="text-muted">기본 열람 범위</dt><dd className="text-right font-semibold text-strong">{folder ? scopeLabel(folder.scope) : "—"}</dd></div>
            <div className="flex justify-between gap-3 py-2.5"><dt className="text-muted">관리 부서</dt><dd className="text-right font-semibold text-strong">{department?.name ?? "—"}</dd></div>
            {department !== null && (
              <div className="flex justify-between gap-3 py-2.5"><dt className="text-muted">개별 부여 문서</dt><dd className="text-right font-semibold text-strong">{department.granted_document_count}건</dd></div>
            )}
          </dl>
        </section>
      </div>
    </div>
  );
}
