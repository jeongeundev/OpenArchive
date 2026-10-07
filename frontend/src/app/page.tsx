"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { DocumentFilters } from "@/components/DocumentFilters";
import { createFolder, listDocumentTags, type DocumentFilters as Filters } from "@/lib/api";
import { DocumentPager } from "@/components/DocumentPager";
import { DocumentTable } from "@/components/DocumentTable";
import { EmptyDocuments } from "@/components/EmptyDocuments";
import { FolderHeader } from "@/components/FolderHeader";
import { FolderTree } from "@/components/FolderTree";
import { PipelineProgress } from "@/components/PipelineProgress";
import { UploadDropzone } from "@/components/UploadDropzone";
import { useAuth } from "@/components/AuthProvider";
import { useDocumentProgress } from "@/lib/useDocumentProgress";
import { useDocuments } from "@/lib/useDocuments";
import { useFolders } from "@/lib/useFolders";

const PAGE_SIZE = 50;

export default function Home(): React.ReactElement {
  // 선택 폴더를 주소(?folder=)에 두므로 useSearchParams가 필요하고, 정적 export는 Suspense 경계를 요구한다.
  return (
    <Suspense fallback={<p className="text-sm text-neutral-500">불러오는 중…</p>}>
      <HomeView />
    </Suspense>
  );
}

function HomeView(): React.ReactElement {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { auth } = useAuth();
  const { folders, loading: foldersLoading, error: foldersError, refresh: refreshFolders } = useFolders(auth.authenticated);
  const folderParam = searchParams?.get("folder") ?? null;
  // 받은 목록에 있는 폴더만 고른다. 서버가 볼 수 없는 폴더를 이미 뺐으므로 없으면 전체 문서로 되돌린다.
  const selectedFolder = folderParam === null ? null : folders.find(folder => folder.id === folderParam) ?? null;
  const folderId = selectedFolder?.id ?? null;
  const [page, setPage] = useState(0);
  const [pageFolderId, setPageFolderId] = useState(folderId);
  if (pageFolderId !== folderId) {
    setPageFolderId(folderId);
    setPage(0);
  }
  const [filters, setFilters] = useState<Filters>({ sort: "updated" });
  const [tags, setTags] = useState<string[]>([]);
  const [tagsRevision, setTagsRevision] = useState(0);
  const { documents, total, loading, error, refresh } = useDocuments({
    ...filters,
    folderId: folderId ?? undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { progress, error: progressError, refresh: refreshProgress } = useDocumentProgress();

  useEffect(() => {
    if (folderParam === null || !auth.authenticated || foldersLoading || foldersError !== null) return;
    if (!folders.some(folder => folder.id === folderParam)) router.replace("/");
  }, [folderParam, folders, foldersLoading, foldersError, auth.authenticated, router]);

  const selectFolder = useCallback((id: string | null) => {
    router.push(id === null ? "/" : `/?folder=${encodeURIComponent(id)}`);
  }, [router]);

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
    setFilters(value);
    setPage(0);
  }, []);
  const hasConditions = Boolean(filters.q?.trim() || filters.contentType || filters.tag);
  const emptyMessage = hasConditions ? "조건에 맞는 문서가 없습니다."
    : folderId !== null ? "이 폴더에 문서가 없습니다." : undefined;

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
        <h1 className="text-4xl font-semibold text-white">문서</h1>
        <p className="mt-3 text-sm text-neutral-400">
          저장된 문서와 임베딩 처리 상태를 확인합니다.
        </p>
      </div>

      <div className={auth.authenticated ? "grid gap-6 md:grid-cols-[13rem_minmax(0,1fr)]" : ""}>
      {auth.authenticated ? (
        <aside>
          <FolderTree folders={folders} selectedId={folderId} onSelect={selectFolder} onCreate={onCreateFolder} />
          <Link href="/trash" className="mt-6 block px-2 text-sm text-neutral-400 hover:text-white">휴지통</Link>
        </aside>
      ) : null}
      <div className="min-w-0 space-y-8">
      {selectedFolder !== null ? (
        <FolderHeader key={selectedFolder.id} folder={selectedFolder} folders={folders}
          onChanged={refreshFolders} onDeleted={onFolderDeleted} onAccessSaved={onFolderAccessSaved} />
      ) : null}

      {auth.authenticated ? <UploadDropzone onUploaded={onUploaded} folders={folders} defaultFolderId={folderId} /> : null}

      <DocumentFilters value={filters} tags={tags} onChange={onFiltersChanged} />

      {globalTotal === 0 && auth.username !== null ? (
        <EmptyDocuments username={auth.username} />
      ) : loading ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : (
        <div className="space-y-4">
          {progress !== null ? <PipelineProgress progress={progress} /> : null}
          <DocumentTable documents={documents} emptyMessage={emptyMessage} />
          {globalTotal === null && progressError !== null ? (
            <p className="text-sm text-neutral-500" role="status">
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
        <p className="text-sm text-neutral-500" role="status">
          {error}
        </p>
      ) : null}
      </div>
      </div>
    </section>
  );
}
