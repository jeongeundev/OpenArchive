// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useId, useMemo, useState } from "react";

import { DocumentFilters } from "@/components/DocumentFilters";
import { DocumentInfoPane } from "@/components/DocumentInfoPane";
import { DocumentList } from "@/components/DocumentList";
import { createFolder, listDocumentTags, type DocumentFilters as Filters } from "@/lib/api";
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
  const [uploadOpen, setUploadOpen] = useState(false);
  const uploadRegionId = useId();
  const { documents, total, loading, error, refresh } = useDocuments({
    ...filters,
    folderId: folderId ?? undefined,
    granted: granted || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { progress, error: progressError, refresh: refreshProgress } = useDocumentProgress();
  // 정보 창에 띄운 문서. 주소에 두지 않는다 — 목록 상태가 아니라 잠깐 들여다보는 것이다.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 폴더가 바뀌면 창을 닫는다. 렌더 중에 맞추는 것은 React가 권하는 「이전 값과 비교」 방식이다.
  const [selectionFolderId, setSelectionFolderId] = useState(folderId);
  if (selectionFolderId !== folderId) {
    setSelectionFolderId(folderId);
    setSelectedId(null);
  }
  // 목록에서 사라진 문서(조건·쪽 변경, 삭제)는 창도 닫는다.
  const shownId = selectedId !== null && documents.some(document => document.id === selectedId) ? selectedId : null;

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
    router.push("/");
    refreshFolders();
  }, [router, refreshFolders]);
  const globalTotal =
    progress === null ? null : Object.values(progress).reduce((sum, count) => sum + count, 0);

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
      <div>
        <h1 className="text-4xl font-semibold text-strong">문서</h1>
        <p className="mt-3 text-sm text-muted">
          저장된 문서와 임베딩 처리 상태를 확인합니다.
        </p>
      </div>

      <div data-testid="document-layout" className={`grid gap-6 ${
        auth.authenticated
          ? shownId !== null ? "lg:grid-cols-[13rem_minmax(0,1fr)_400px]" : "lg:grid-cols-[13rem_minmax(0,1fr)]"
          : shownId !== null ? "lg:grid-cols-[minmax(0,1fr)_400px]" : ""
      }`}>
      {auth.authenticated ? (
        <aside>
          <FolderTree folders={folders} selectedId={folderId} granted={granted}
            onSelect={selectFolder} onSelectGranted={selectGranted} onCreate={onCreateFolder} />
          <Link href="/trash" className="mt-6 block px-2 text-sm text-muted hover:text-strong">휴지통</Link>
        </aside>
      ) : null}
      <div className="min-w-0 space-y-8">
      {selectedFolder !== null ? (
        <FolderHeader key={selectedFolder.id} folder={selectedFolder} folders={folders}
          onChanged={refreshFolders} onDeleted={onFolderDeleted} onAccessSaved={onFolderAccessSaved} />
      ) : null}

      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          {auth.authenticated ? (
            <button type="button" aria-expanded={uploadOpen} aria-controls={uploadRegionId}
              onClick={() => setUploadOpen(open => !open)}
              className="whitespace-nowrap rounded-lg bg-strong px-4 py-2 text-sm font-medium text-on-strong hover:bg-strong/90">
              {uploadOpen ? "업로드 닫기" : "문서 올리기"}
            </button>
          ) : null}
          <div className="min-w-0 flex-1">
            <DocumentFilters value={filters} tags={tags} onChange={onFiltersChanged} />
          </div>
        </div>
        {auth.authenticated ? (
          <div id={uploadRegionId}>
            {uploadOpen ? <UploadDropzone onUploaded={onUploaded} folders={folders} defaultFolderId={folderId} /> : null}
          </div>
        ) : null}
      </div>

      {globalTotal === 0 && auth.username !== null ? (
        <EmptyDocuments username={auth.username} />
      ) : loading ? (
        <p className="text-sm text-muted">불러오는 중…</p>
      ) : (
        <div className="space-y-4">
          {progress !== null ? <PipelineProgress progress={progress} /> : null}
          <DocumentList documents={documents} emptyMessage={emptyMessage} selectedId={shownId} onSelect={setSelectedId} />
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
      {shownId !== null ? <DocumentInfoPane key={shownId} documentId={shownId} onClose={() => setSelectedId(null)} /> : null}
      </div>
    </section>
  );
}
