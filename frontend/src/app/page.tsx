"use client";

import { useCallback, useState } from "react";

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
  const { documents, loading, error, refresh } = useDocuments({
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { progress, error: progressError, refresh: refreshProgress } = useDocumentProgress();
  const { auth } = useAuth();
  const total =
    progress === null ? null : Object.values(progress).reduce((sum, count) => sum + count, 0);

  const onUploaded = useCallback(() => {
    refresh();
    refreshProgress();
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

      {total === 0 && auth.username !== null ? (
        <EmptyDocuments username={auth.username} />
      ) : loading ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : (
        <div className="space-y-4">
          {progress !== null ? <PipelineProgress progress={progress} /> : null}
          <DocumentTable documents={documents} />
          {total === null && progressError !== null ? (
            <p className="text-sm text-neutral-500" role="status">
              처리 현황을 불러오지 못했습니다 — 전체 문서 수와 페이지를 표시할 수 없습니다.
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
