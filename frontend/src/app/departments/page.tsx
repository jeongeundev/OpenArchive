// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { folderChildren, scopeLabel } from "@/lib/folders";
import { useFolders } from "@/lib/useFolders";
import type { Folder } from "@/lib/types";

/**
 * 부서 (읽기 전용) — 볼 수 있는 최상위 폴더를 부서 폴더·공용 폴더 카드로 보인다.
 * 폴더 목록은 이미 열람 술어를 거친 것이라, 수는 보이는 폴더만 더한다. 관리자 그룹 API는 부르지 않는다.
 */
export default function DepartmentsPage(): React.ReactElement {
  const router = useRouter();
  const { auth } = useAuth();
  const { folders, loading, error } = useFolders(true);
  const [picked, setPicked] = useState<string | null>(null);

  const ids = new Set(folders.map(folder => folder.id));
  const roots = folders.filter(folder => folder.parent_id === null || !ids.has(folder.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));
  const descendants = (folder: Folder): Folder[] =>
    folderChildren(folders, folder.id).flatMap(child => [child, ...descendants(child)]);
  const documents = (folder: Folder): number =>
    folder.document_count + descendants(folder).reduce((sum, child) => sum + child.document_count, 0);
  const title = (folder: Folder): string => folder.department?.name ?? folder.name;

  const mine = auth.department?.folder_id ?? null;
  const selected = roots.find(folder => folder.id === picked)
    ?? roots.find(folder => folder.id === mine) ?? roots[0] ?? null;

  function tree(parent: Folder): React.ReactNode[] {
    return folderChildren(folders, parent.id).map(child => {
      const children = tree(child);
      return (
        <li key={child.id}>
          <button type="button" onClick={() => router.push(`/documents?folder=${encodeURIComponent(child.id)}`)}
            className="flex w-full items-center justify-between rounded-md px-3 py-1.5 text-left text-sm text-body hover:bg-card">
            <span className="truncate">{child.name}</span>
            <span className="text-xs text-muted">{documents(child)}</span>
          </button>
          {children.length > 0 ? <ul className="ml-3 border-l border-line pl-2">{children}</ul> : null}
        </li>
      );
    });
  }

  const subfolders = selected === null ? [] : tree(selected);

  return (
    <section className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-4xl font-semibold text-strong">부서</h1>
          <p className="mt-3 text-sm text-muted">부서별 폴더와 열람 범위를 봅니다.</p>
        </div>
        {auth.is_admin && (
          <Link href="/admin/groups" className="rounded-md border border-line-strong bg-card px-4 py-2 text-sm font-medium text-strong">
            그룹 관리
          </Link>
        )}
      </div>

      {error !== null ? <p className="text-sm text-failed">{error}</p>
        : loading ? <p className="text-sm text-muted">불러오는 중…</p>
        : selected === null ? <p className="text-sm text-muted">볼 수 있는 부서 폴더가 없습니다.</p>
        : (
          <>
            <div className="grid gap-4 lg:grid-cols-4">
              {roots.map(folder => (
                <button key={folder.id} type="button" onClick={() => setPicked(folder.id)} aria-pressed={folder.id === selected.id}
                  className={`rounded-xl border p-5 text-left ${folder.id === selected.id ? "border-accent bg-accent-soft" : "border-line bg-card"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-semibold text-strong">{title(folder)}</span>
                    <span className="shrink-0 rounded-full bg-sunken px-2 py-0.5 text-xs text-muted">
                      {folder.department ? "부서 폴더" : "공용 폴더"}
                    </span>
                  </div>
                  <p className="mt-3 text-sm text-muted">문서 {documents(folder)} · 폴더 {descendants(folder).length}</p>
                </button>
              ))}
            </div>

            <div className="rounded-xl border border-line bg-card p-6">
              <div className="flex items-center justify-between gap-4">
                <h2 className="text-xl font-semibold text-strong">{title(selected)}</h2>
                <Link href={`/documents?folder=${encodeURIComponent(selected.id)}`}
                  className="rounded-md bg-strong px-4 py-2 text-sm font-medium text-on-strong">
                  문서 보기
                </Link>
              </div>
              <div className="mt-6 grid gap-6 lg:grid-cols-2">
                <section aria-labelledby="department-tree">
                  <h3 id="department-tree" className="text-sm font-semibold text-strong">폴더 구조</h3>
                  <div className="mt-3 rounded-lg bg-sunken p-2">
                    {subfolders.length ? <ul>{subfolders}</ul> : <p className="px-3 py-1.5 text-sm text-muted">하위 폴더가 없습니다.</p>}
                  </div>
                </section>
                <section aria-labelledby="department-access">
                  <h3 id="department-access" className="text-sm font-semibold text-strong">열람 권한</h3>
                  <dl className="mt-3 space-y-2 text-sm">
                    <div className="flex gap-3"><dt className="w-28 shrink-0 text-muted">기본 열람 범위</dt><dd className="text-body">{scopeLabel(selected.scope)}</dd></div>
                    {selected.department && (
                      <div className="flex gap-3"><dt className="w-28 shrink-0 text-muted">관리 부서</dt><dd className="text-body">{selected.department.name}</dd></div>
                    )}
                  </dl>
                </section>
              </div>
            </div>
          </>
        )}
    </section>
  );
}
