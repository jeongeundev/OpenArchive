"use client";

import { useState } from "react";

import { ApiError, editDocument } from "@/lib/api";
import type { DocumentDetail, ResolvedLink } from "@/lib/types";
import { WikilinkContent } from "./WikilinkContent";

export function TextEditor({
  document,
  onSaved,
  onEditingChange,
  disabled,
  links = [],
}: {
  document: DocumentDetail;
  onSaved: () => void;
  onEditingChange: (editing: boolean) => void;
  disabled: boolean;
  links?: ResolvedLink[];
}): React.ReactElement {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(document.content);
  // 편집을 시작한 시점의 버전. 조건부 폴링이 편집 도중 document를 갱신하므로,
  // 저장 시 document.version을 그대로 쓰면 남의 변경을 409 없이 덮어쓴다.
  const [editingVersion, setEditingVersion] = useState(document.version);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const showsExtractionNotice =
    document.content_type === "pdf" || document.content_type === "docx";

  function changeEditing(nextEditing: boolean): void {
    if (nextEditing) {
      setContent(document.content);
      setEditingVersion(document.version);
    }
    setEditing(nextEditing);
    onEditingChange(nextEditing);
  }

  function cancel(): void {
    setContent(document.content);
    setError(null);
    changeEditing(false);
  }

  async function save(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (saving) return;

    setSaving(true);
    setError(null);
    try {
      await editDocument(document.id, { content, version: editingVersion });
      changeEditing(false);
      onSaved();
    } catch (reason: unknown) {
      if (reason instanceof ApiError) {
        const version = reason.status === 409 && reason.currentVersion !== undefined
          ? ` 현재 서버 버전: v${reason.currentVersion}`
          : "";
        setError(`${reason.detail}${version}`);
      } else {
        setError("추출 텍스트를 저장하지 못했습니다.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-medium text-neutral-400">추출 텍스트</h2>
        {!editing && !disabled ? (
          <button
            className="text-sm text-neutral-500 hover:text-neutral-300 disabled:cursor-not-allowed disabled:text-neutral-600"
            disabled={disabled}
            onClick={() => changeEditing(true)}
            type="button"
          >
            편집
          </button>
        ) : null}
      </div>
      {showsExtractionNotice ? (
        <p className="text-sm text-neutral-500">
          원본 파일이 아니라 업로드 시 추출된 텍스트를 편집합니다. 저장하면 새 텍스트 버전이
          만들어집니다.
        </p>
      ) : null}
      {editing ? (
        <form className="space-y-3" onSubmit={(event) => void save(event)}>
          <label className="sr-only" htmlFor="extracted-text">추출 텍스트</label>
          <textarea
            className="min-h-80 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-sm leading-relaxed text-neutral-300"
            id="extracted-text"
            onChange={(event) => setContent(event.target.value)}
            value={content}
          />
          <p className="text-xs text-neutral-500">
            다른 문서를 연결하려면 [[문서 제목]] 형식으로 입력하세요.
          </p>
          {error !== null ? <p className="text-sm text-[#ef4444]">{error}</p> : null}
          <div className="flex gap-3">
            <button
              className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
              disabled={saving}
              type="submit"
            >
              {saving ? "저장 중…" : "저장"}
            </button>
            <button
              className="text-sm text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
              disabled={saving}
              onClick={cancel}
              type="button"
            >
              취소
            </button>
          </div>
        </form>
      ) : (
        <div className="whitespace-pre-wrap rounded-lg border border-neutral-800 bg-[#141414] p-6 text-sm leading-relaxed text-neutral-300">
          <WikilinkContent content={document.content} links={links} />
        </div>
      )}
    </section>
  );
}
