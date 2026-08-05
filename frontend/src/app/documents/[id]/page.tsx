"use client";

import Link from "next/link";
import { use } from "react";

import { DocumentMeta } from "@/components/DocumentMeta";
import { VersionHistory } from "@/components/VersionHistory";
import { useDocument } from "@/lib/useDocument";

export default function DocumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.ReactElement {
  const { id } = use(params);
  const { document, loading, error } = useDocument(id);

  if (loading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (document === null) {
    return <p className="text-sm text-neutral-500">{error ?? "문서를 찾을 수 없습니다."}</p>;
  }

  return (
    <div className="space-y-8">
      <Link href="/" className="text-sm text-neutral-500 hover:text-neutral-300">
        ← 목록으로 돌아가기
      </Link>

      <DocumentMeta document={document} />

      <section className="space-y-4">
        <h2 className="text-sm font-medium text-neutral-400">추출 텍스트</h2>
        <div className="whitespace-pre-wrap rounded-lg border border-neutral-800 bg-[#141414] p-6 text-sm leading-relaxed text-neutral-300">
          {document.content}
        </div>
      </section>

      <VersionHistory versions={document.versions} currentVersion={document.version} />

      {/* M4에서 관련 문서 API 연동으로 교체한다. */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-neutral-400">관련 문서</h2>
        <p className="text-sm text-neutral-500">임베딩이 완료되면 표시됩니다.</p>
      </section>

      {/* M4에서 태그 추천 API 연동으로 교체한다. */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-neutral-400">태그 추천</h2>
        <p className="text-sm text-neutral-500">임베딩이 완료되면 표시됩니다.</p>
      </section>

      {error !== null ? (
        <p className="text-sm text-neutral-500" role="status">
          {error}
        </p>
      ) : null}
    </div>
  );
}
