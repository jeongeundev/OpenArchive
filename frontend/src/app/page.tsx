"use client";

import { useCallback, useEffect, useState } from "react";

import { DocumentFilters } from "@/components/DocumentFilters";
import { listDocumentTags, type DocumentFilters as Filters } from "@/lib/api";
import { DocumentPager } from "@/components/DocumentPager";
import { DocumentTable } from "@/components/DocumentTable";
import { EmptyDocuments } from "@/components/EmptyDocuments";
import { PipelineProgress } from "@/components/PipelineProgress";
import { UploadDropzone } from "@/components/UploadDropzone";
import { useAuth } from "@/components/AuthProvider";
import { useDocumentProgress } from "@/lib/useDocumentProgress";
import { useDocuments } from "@/lib/useDocuments";

const PAGE_SIZE = 50;

export default function Home(): React.ReactElement {
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<Filters>({ sort: "updated" });
  const [tags, setTags] = useState<string[]>([]);
  const [tagsRevision, setTagsRevision] = useState(0);
  const { documents, total, loading, error, refresh } = useDocuments({
    ...filters,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { progress, error: progressError, refresh: refreshProgress } = useDocumentProgress();
  const { auth } = useAuth();
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
  const hasFilters = Boolean(filters.q?.trim() || filters.contentType || filters.tag);

  const onUploaded = useCallback(() => {
    refresh();
    refreshProgress();
    setTagsRevision((revision) => revision + 1);
  }, [refresh, refreshProgress]);

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">문서</h1>
        <p className="mt-3 text-sm text-neutral-400">
          저장된 문서와 임베딩 처리 상태를 확인합니다.
        </p>
      </div>

      {auth.authenticated ? <UploadDropzone onUploaded={onUploaded} /> : null}

      <DocumentFilters value={filters} tags={tags} onChange={onFiltersChanged} />

      {globalTotal === 0 && auth.username !== null ? (
        <EmptyDocuments username={auth.username} />
      ) : loading ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : (
        <div className="space-y-4">
          {progress !== null ? <PipelineProgress progress={progress} /> : null}
          <DocumentTable documents={documents} emptyMessage={hasFilters ? "조건에 맞는 문서가 없습니다." : undefined} />
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
    </section>
  );
}
