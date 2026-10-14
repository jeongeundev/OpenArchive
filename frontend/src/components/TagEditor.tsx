// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useState } from "react";

export function TagEditor({
  tags,
  disabled,
  saving,
  error,
  onChange,
  onSave,
}: {
  tags: string[];
  disabled: boolean;
  saving: boolean;
  error: string | null;
  onChange: (tags: string[]) => void;
  onSave: () => void;
}): React.ReactElement {
  const [input, setInput] = useState("");

  function addTag(): void {
    const tag = input.trim();
    if (tag === "" || tags.includes(tag)) return;
    onChange([...tags, tag]);
    setInput("");
  }

  const controlsDisabled = disabled || saving;

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-muted">태그</h2>
      <div className="flex flex-wrap gap-2">
        {tags.map((tag) => (
          <span
            className="flex items-center gap-2 rounded bg-sunken px-2 py-1 text-xs text-body"
            key={tag}
          >
            {tag}
            <button
              aria-label={`${tag} 태그 삭제`}
              className="text-muted hover:text-strong disabled:text-faint"
              disabled={controlsDisabled}
              onClick={() => onChange(tags.filter((item) => item !== tag))}
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
          className="rounded-lg border border-line bg-card px-4 py-3 text-sm text-body disabled:text-faint"
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
          className="text-sm text-muted hover:text-strong disabled:text-faint"
          disabled={controlsDisabled}
          onClick={addTag}
          type="button"
        >
          태그 추가
        </button>
        <button
          className="rounded-lg bg-strong px-4 py-2 text-sm text-on-strong hover:bg-strong/90 disabled:bg-sunken disabled:text-faint"
          disabled={controlsDisabled}
          onClick={onSave}
          type="button"
        >
          {saving ? "저장 중…" : "태그 저장"}
        </button>
      </div>
      {error !== null ? <p className="text-sm text-failed">{error}</p> : null}
    </section>
  );
}
