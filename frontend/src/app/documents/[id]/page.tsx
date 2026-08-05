"use client";

import Link from "next/link";
import { use, useState } from "react";

import { DocumentActions } from "@/components/DocumentActions";
import { DocumentMeta } from "@/components/DocumentMeta";
import { RelatedDocuments } from "@/components/RelatedDocuments";
import { TagSuggestions } from "@/components/TagSuggestions";
import { TextEditor } from "@/components/TextEditor";
import { VersionHistory } from "@/components/VersionHistory";
import { useDocument } from "@/lib/useDocument";
import { useRelated } from "@/lib/useRelated";
import { getCurrentUser } from "@/lib/user";

export default function DocumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.ReactElement {
  const { id } = use(params);
  const { document, loading, error, refresh } = useDocument(id);
  const relatedData = useRelated(id, document?.chunk_version ?? null);
  const [editing, setEditing] = useState(false);
  const anonymous = getCurrentUser() === null;

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

      <DocumentActions
        disabled={anonymous || editing}
        document={document}
        onChanged={refresh}
      />

      <TextEditor
        disabled={anonymous}
        document={document}
        onEditingChange={setEditing}
        onSaved={refresh}
      />

      <VersionHistory versions={document.versions} currentVersion={document.version} />

      {relatedData.related !== null ? (
        <RelatedDocuments response={relatedData.related} />
      ) : (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-neutral-400">관련 문서</h2>
          <p className="text-sm text-neutral-500">
            {relatedData.loading ? "불러오는 중…" : "관련 문서를 불러오지 못했습니다."}
          </p>
        </section>
      )}

      {relatedData.suggestions !== null ? (
        <TagSuggestions response={relatedData.suggestions} />
      ) : (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-neutral-400">태그 추천</h2>
          <p className="text-sm text-neutral-500">
            {relatedData.loading ? "불러오는 중…" : "태그 추천을 불러오지 못했습니다."}
          </p>
        </section>
      )}

      {relatedData.error !== null ? (
        <p className="text-sm text-neutral-500" role="status">
          {relatedData.error}
        </p>
      ) : null}

      {error !== null ? (
        <p className="text-sm text-neutral-500" role="status">
          {error}
        </p>
      ) : null}
    </div>
  );
}
