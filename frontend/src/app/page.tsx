"use client";

import { DocumentTable } from "@/components/DocumentTable";
import { UploadDropzone } from "@/components/UploadDropzone";
import { useDocuments } from "@/lib/useDocuments";

export default function Home(): React.ReactElement {
  const { documents, loading, error, refresh } = useDocuments();

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">문서</h1>
        <p className="mt-3 text-sm text-neutral-400">
          저장된 문서와 임베딩 처리 상태를 확인합니다.
        </p>
      </div>

      <UploadDropzone onUploaded={refresh} />

      {loading ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : (
        <DocumentTable documents={documents} />
      )}

      {error !== null && !loading ? (
        <p className="text-sm text-neutral-500" role="status">
          {error}
        </p>
      ) : null}
    </section>
  );
}
