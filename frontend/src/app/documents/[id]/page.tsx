"use client";

import Link from "next/link";
import { use, useState } from "react";

import { DocumentActions } from "@/components/DocumentActions";
import { DocumentMeta } from "@/components/DocumentMeta";
import { RelatedDocuments } from "@/components/RelatedDocuments";
import { TagEditor } from "@/components/TagEditor";
import { TagSuggestions } from "@/components/TagSuggestions";
import { TextEditor } from "@/components/TextEditor";
import { VersionHistory } from "@/components/VersionHistory";
import { useDocument } from "@/lib/useDocument";
import { useRelated } from "@/lib/useRelated";
import { ApiError, updateTags } from "@/lib/api";
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
  const [tagApplyError, setTagApplyError] = useState<string | null>(null);
  const [applyingTag, setApplyingTag] = useState(false);
  const anonymous = getCurrentUser() === null;

  if (loading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (document === null) {
    return <p className="text-sm text-neutral-500">{error ?? "문서를 찾을 수 없습니다."}</p>;
  }
  const currentTags = document.tags;

  async function applyTag(tag: string): Promise<void> {
    if (applyingTag) return;
    setApplyingTag(true);
    setTagApplyError(null);
    try {
      await updateTags(id, [...currentTags, tag]);
      refresh();
      relatedData.refresh();
    } catch (reason: unknown) {
      setTagApplyError(
        reason instanceof ApiError ? reason.detail : "추천 태그를 적용하지 못했습니다.",
      );
    } finally {
      setApplyingTag(false);
    }
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

      <TagEditor
        disabled={anonymous || editing}
        document={document}
        key={`${document.id}:${document.tags.join("\u0000")}`}
        onSaved={() => {
          refresh();
          relatedData.refresh();
        }}
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
        <TagSuggestions
          onApply={anonymous || editing || applyingTag ? undefined : applyTag}
          response={relatedData.suggestions}
        />
      ) : (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-neutral-400">태그 추천</h2>
          <p className="text-sm text-neutral-500">
            {relatedData.loading ? "불러오는 중…" : "태그 추천을 불러오지 못했습니다."}
          </p>
        </section>
      )}

      {tagApplyError !== null ? (
        <p className="text-sm text-[#ef4444]">{tagApplyError}</p>
      ) : null}

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
