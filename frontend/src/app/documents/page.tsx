// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useId, useMemo, useState } from "react";

import { DocumentFilters } from "@/components/DocumentFilters";
import { DocumentList } from "@/components/DocumentList";
import { countDocuments, createFolder, listDocumentTags, type DocumentFilters as Filters } from "@/lib/api";
import { DocumentPager } from "@/components/DocumentPager";
import { EmptyDocuments } from "@/components/EmptyDocuments";
import { FolderHeader } from "@/components/FolderHeader";
import { FolderTree } from "@/components/FolderTree";
import { PipelineProgress } from "@/components/PipelineProgress";
import { UploadDropzone } from "@/components/UploadDropzone";
import { useAuth } from "@/components/AuthProvider";
import { useDocumentProgress } from "@/lib/useDocumentProgress";
import { useDocuments } from "@/lib/useDocuments";
import { useFolders } from "@/lib/useFolders";
import { listHref, listStateFromParams, rememberListHref } from "@/lib/urlState";

import { SUPPORTED_CONTENT_TYPES, type ContentType } from "@/lib/types";

const PAGE_SIZE = 50;

export default function Home(): React.ReactElement {
  // 선택 폴더·조건·쪽을 주소에 두므로 useSearchParams가 필요하고, 정적 export는 Suspense 경계를 요구한다.
  return (
    <Suspense fallback={<p className="text-sm text-muted">불러오는 중…</p>}>
      <HomeView />
    </Suspense>
  );
}

function HomeView(): React.ReactElement {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { auth } = useAuth();
  const { folders, loading: foldersLoading, error: foldersError, refresh: refreshFolders } = useFolders(auth.authenticated);
  // 폴더·조건·쪽은 주소가 기준이다 — 문서로 갔다가 뒤로 오면 같은 목록을 다시 그린다.
  const query = searchParams?.toString() ?? "";
  const listState = useMemo(() => listStateFromParams(new URLSearchParams(query)), [query]);
  const { filters, page } = listState;
  const granted = listState.granted === true;
  const folderParam = listState.folderId;
  // 받은 목록에 있는 폴더만 고른다. 서버가 볼 수 없는 폴더를 이미 뺐으므로 없으면 전체 문서로 되돌린다.
  const selectedFolder = folderParam === null ? null : folders.find(folder => folder.id === folderParam) ?? null;
  const folderId = selectedFolder?.id ?? null;
  const [tags, setTags] = useState<string[]>([]);
  const [tagsRevision, setTagsRevision] = useState(0);
  // 업로드 폼은 접어 두고 목록을 먼저 보인다. 올린 뒤에도 이어서 올릴 수 있게 펼친 채로 둔다.
  const [uploadOpen, setUploadOpen] = useState(searchParams?.get("upload") === "1");
  const [creating, setCreating] = useState(false);
  const [types, setTypes] = useState<ContentType[]>([]);
  const uploadRegionId = useId();
  const { documents, total, loading, error, refresh } = useDocuments({
    ...filters,
    folderId: folderId ?? undefined,
    granted: granted || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { progress, error: progressError, refresh: refreshProgress } = useDocumentProgress();
  useEffect(() => {
    if (folderParam === null || !auth.authenticated || foldersLoading || foldersError !== null) return;
    if (!folders.some(folder => folder.id === folderParam)) router.replace(listHref({ folderId: null, filters, page: 0 }));
  }, [folderParam, folders, foldersLoading, foldersError, auth.authenticated, router, filters]);

  useEffect(() => rememberListHref(listHref(listState)), [listState]);

  // 폴더 이동은 기록에 남기고, 조건·쪽 변경은 남기지 않는다 — 제목 입력이 멈출 때마다 뒤로 가기 한 칸이 쌓이지 않게.
  const selectFolder = useCallback((id: string | null) => {
    router.push(listHref({ folderId: id, filters, page: 0 }));
  }, [router, filters]);

  const selectGranted = useCallback(() => {
    router.push(listHref({ folderId: null, filters, page: 0, granted: true }));
  }, [router, filters]);

  const setPage = useCallback((next: number) => {
    router.replace(listHref({ ...listState, page: next }));
  }, [router, listState]);

  const onCreateFolder = useCallback(async (name: string, parentId: string | null) => {
    await createFolder({ name, parentId });
    refreshFolders();
  }, [refreshFolders]);

  const onFolderDeleted = useCallback(() => {
    router.push("/documents");
    refreshFolders();
  }, [router, refreshFolders]);
  const globalTotal =
    progress === null ? null : Object.values(progress).reduce((sum, count) => sum + count, 0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all(SUPPORTED_CONTENT_TYPES.filter(type => type !== "jpeg").map(async type => ({ type, count: await countDocuments({ contentType: type }, controller.signal) })))
      .then(results => { if (!controller.signal.aborted) setTypes(results.filter(item => item.count > 0).map(item => item.type)); }).catch(() => {});
    return () => controller.abort();
  }, [tagsRevision]);
  const inFlight = progress ? progress.extracting + progress.pending + progress.processing : 0;

  useEffect(() => {
    const controller = new AbortController();
    listDocumentTags(controller.signal).then((result) => {
      if (!controller.signal.aborted) setTags(result);
    }).catch(() => {});
    return () => controller.abort();
  }, [tagsRevision]);

  const onFiltersChanged = useCallback((value: Filters) => {
    router.replace(listHref({ folderId: folderParam, filters: value, page: 0, granted }));
  }, [router, folderParam, granted]);
  const hasConditions = Boolean(filters.q?.trim() || filters.contentType || filters.tag);
  const emptyMessage = hasConditions ? "조건에 맞는 문서가 없습니다."
    : folderId !== null ? "이 폴더에 문서가 없습니다."
    : granted ? "부여받은 문서가 없습니다." : undefined;

  const onUploaded = useCallback(() => {
    refresh();
    refreshProgress();
    refreshFolders();
    setTagsRevision((revision) => revision + 1);
  }, [refresh, refreshProgress, refreshFolders]);

  // 폴더 범위가 바뀌면 문서 수·목록도 바뀔 수 있다.
  const onFolderAccessSaved = useCallback(() => {
    refresh();
    refreshProgress();
    refreshFolders();
  }, [refresh, refreshProgress, refreshFolders]);

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-3xl font-bold text-strong">문서</h1>
          <p className="mt-2 text-sm text-muted">{globalTotal !== null ? `내가 열람할 수 있는 문서 ${globalTotal}건` : "문서 수를 확인하는 중…"}{inFlight > 0 ? ` · 처리 중 ${inFlight}` : progress && progress.ready === globalTotal && globalTotal !== 0 ? " · 모두 검색 가능" : ""}</p>
        </div>
        {auth.authenticated && <div className="flex gap-2">
          <button type="button" onClick={() => setCreating(open => !open)} className="rounded-lg border border-line-strong px-4 py-2 text-sm text-body">새 폴더</button>
          <button type="button" aria-expanded={uploadOpen} aria-controls={uploadRegionId} onClick={() => setUploadOpen(open => !open)} className="rounded-lg bg-strong px-4 py-2 text-sm text-on-strong">{uploadOpen ? "업로드 닫기" : "문서 올리기"}</button>
        </div>}
      </div>
      {auth.authenticated && <nav aria-label="문서 보기" className="flex gap-6 border-b border-line text-sm">
        <button type="button" aria-current={!granted ? "page" : undefined} onClick={() => selectFolder(null)} className={`border-b-2 pb-3 ${!granted ? "border-strong font-semibold text-strong" : "border-transparent text-muted"}`}>전체 문서</button>
        <button type="button" aria-current={granted ? "page" : undefined} onClick={selectGranted} className={`border-b-2 pb-3 ${granted ? "border-strong font-semibold text-strong" : "border-transparent text-muted"}`}>부여받은 문서</button>
        <Link href="/trash" className="pb-3 text-muted">휴지통</Link>
      </nav>}
      <div id={uploadRegionId}>{auth.authenticated && uploadOpen ? <UploadDropzone onUploaded={onUploaded} folders={folders} defaultFolderId={folderId} /> : null}</div>
      <div data-testid="document-layout" className={`grid gap-6 ${auth.authenticated ? "lg:grid-cols-[13rem_minmax(0,1fr)]" : ""}`}>
        {auth.authenticated && <aside className="min-w-0"><h2 className="mb-3 text-sm font-semibold text-muted">폴더</h2>
          <FolderTree folders={folders} selectedId={folderId} granted={granted} total={globalTotal} creating={creating} onCreatingChange={setCreating} onSelect={selectFolder} onCreate={onCreateFolder} />
        </aside>}
        <div className="min-w-0 space-y-5">
      {selectedFolder !== null ? (
        <FolderHeader key={selectedFolder.id} folder={selectedFolder} folders={folders}
          onChanged={refreshFolders} onDeleted={onFolderDeleted} onAccessSaved={onFolderAccessSaved} />
      ) : null}

      <DocumentFilters value={filters} tags={tags} types={types} onChange={onFiltersChanged} />
      {total !== null && <p aria-label="목록 건수" className="text-sm text-muted" role="status">{selectedFolder?.name ?? (granted ? "부여받은 문서" : "전체 문서")} · {total}건</p>}
      {globalTotal === 0 && auth.username !== null ? (
        <EmptyDocuments username={auth.username} />
      ) : loading ? (
        <p className="text-sm text-muted">불러오는 중…</p>
      ) : (
        <div className="space-y-4">
          {progress !== null && inFlight > 0 ? <PipelineProgress progress={progress} /> : null}
          <DocumentList documents={documents} emptyMessage={emptyMessage} />
          {globalTotal === null && progressError !== null ? (
            <p className="text-sm text-muted" role="status">
              처리 현황을 불러오지 못했습니다 — 전체 문서 수를 표시할 수 없습니다.
              ({progressError})
            </p>
          ) : null}
          {total !== null ? (
            <DocumentPager
              onChange={setPage}
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
            />
          ) : null}
        </div>
      )}

      {error !== null && !loading ? (
        <p className="text-sm text-muted" role="status">
          {error}
        </p>
      ) : null}
      </div>
      </div>
    </section>
  );
}
