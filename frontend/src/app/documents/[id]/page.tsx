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
import { useAuth } from "@/components/AuthProvider";

export default function DocumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.ReactElement {
  const { id } = use(params);
  const { document, loading, error, refresh } = useDocument(id);
  const relatedData = useRelated(id, document?.chunk_version ?? null);
  const [editing, setEditing] = useState(false);
  const [draftTags, setDraftTags] = useState<string[] | null>(null);
  const [savingTags, setSavingTags] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const { auth } = useAuth();
  const anonymous = !auth.authenticated;

  if (loading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (document === null) {
    return <p className="text-sm text-neutral-500">{error ?? "문서를 찾을 수 없습니다."}</p>;
  }
  // 화면에 보이는 태그 목록. 저장 전 편집분이 있으면 그쪽이 기준이다 — 추천 적용도
  // 이 목록 위에 얹어야 편집 중이던 태그가 조용히 사라지지 않는다.
  const tags = draftTags ?? document.tags;

  // 태그 저장은 여기 한 곳에서만 한다. 편집 저장과 추천 적용이 각자 updateTags를
  // 부르면 서로 다른 목록을 기준으로 삼게 된다.
  async function saveTags(next: string[]): Promise<void> {
    if (savingTags) return;
    setSavingTags(true);
    setTagError(null);
    try {
      await updateTags(id, next);
      setDraftTags(next);
      refresh();
      relatedData.refresh();
    } catch (reason: unknown) {
      // 실패해도 편집 중이던 태그는 지우지 않는다 (TextEditor의 409 처리와 같은 원칙).
      setTagError(
        reason instanceof ApiError ? reason.detail : "태그를 저장하지 못했습니다.",
      );
    } finally {
      setSavingTags(false);
    }
  }

  return (
    <div className="space-y-8">
      <Link href="/" className="text-sm text-neutral-500 hover:text-neutral-300">
        ← 목록으로 돌아가기
      </Link>

      <DocumentMeta document={document} />

      {!anonymous ? (
        <>
          <DocumentActions disabled={editing} document={document} onChanged={refresh} />
          <TagEditor disabled={editing} error={tagError} onChange={setDraftTags} onSave={() => void saveTags(tags)} saving={savingTags} tags={tags} />
        </>
      ) : null}

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
          onApply={
            anonymous || editing || savingTags
              ? undefined
              : (tag) => saveTags([...tags, tag])
          }
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
