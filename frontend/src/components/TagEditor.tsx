"use client";

import { useState } from "react";

import { ApiError, updateTags } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";

export function TagEditor({
  document,
  disabled,
  onSaved,
}: {
  document: DocumentDetail;
  disabled: boolean;
  onSaved: () => void;
}): React.ReactElement {
  const [tags, setTags] = useState(document.tags);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addTag(): void {
    const tag = input.trim();
    if (tag === "" || tags.includes(tag)) return;
    setTags([...tags, tag]);
    setInput("");
  }

  async function save(): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await updateTags(document.id, tags);
      onSaved();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "태그를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  const controlsDisabled = disabled || saving;

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">태그</h2>
      <div className="flex flex-wrap gap-2">
        {tags.map((tag) => (
          <span
            className="flex items-center gap-2 rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-300"
            key={tag}
          >
            {tag}
            <button
              aria-label={`${tag} 태그 삭제`}
              className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
              disabled={controlsDisabled}
              onClick={() => setTags(tags.filter((item) => item !== tag))}
              type="button"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-3">
        <label className="sr-only" htmlFor="tag-input">태그</label>
        <input
          className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-sm text-neutral-300 disabled:text-neutral-600"
          disabled={controlsDisabled}
          id="tag-input"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addTag();
            }
          }}
          value={input}
        />
        <button
          className="text-sm text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
          disabled={controlsDisabled}
          onClick={addTag}
          type="button"
        >
          태그 추가
        </button>
        <button
          className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
          disabled={controlsDisabled}
          onClick={() => void save()}
          type="button"
        >
          {saving ? "저장 중…" : "태그 저장"}
        </button>
      </div>
      {error !== null ? <p className="text-sm text-[#ef4444]">{error}</p> : null}
    </section>
  );
}
