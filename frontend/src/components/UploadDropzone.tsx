"use client";

import { useRef, useState } from "react";

import { ApiError, uploadDocument } from "@/lib/api";
import { SUPPORTED_CONTENT_TYPES, type Visibility } from "@/lib/types";

export function UploadDropzone({
  onUploaded,
}: {
  onUploaded: () => void;
}): React.ReactElement {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function selectFile(nextFile: File | null): void {
    setFile(nextFile);
    setMessage(null);
    setError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (file === null || uploading) return;

    setUploading(true);
    setMessage(null);
    setError(null);
    try {
      await uploadDocument({
        file,
        title: title.trim() === "" ? undefined : title.trim(),
        tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
        visibility,
      });
      setFile(null);
      setTitle("");
      setTags("");
      setVisibility("public");
      if (inputRef.current !== null) inputRef.current.value = "";
      setMessage("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다.");
      onUploaded();
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "업로드에 실패했습니다.");
    } finally {
      setUploading(false);
    }
  }

  const disabled = uploading;

  return (
    <form
      className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6"
      onSubmit={(event) => void submit(event)}
    >
      <div>
        <h2 className="text-sm font-medium text-neutral-400">문서 업로드</h2>
        <p className="mt-2 text-sm text-neutral-500">
          업로드한 파일에서 텍스트만 추출해 저장합니다. 원본 파일은 보관하지 않습니다.
        </p>
      </div>

      <label
        className={`block cursor-pointer rounded-lg border border-dashed p-6 text-sm ${
          dragging ? "border-[#0ea5e9]" : "border-neutral-700"
        } ${disabled ? "cursor-not-allowed text-neutral-600" : "text-neutral-300"}`}
        htmlFor="document-file"
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) selectFile(event.dataTransfer.files[0] ?? null);
        }}
      >
        <span>{file === null ? "파일을 끌어놓거나 클릭해 선택하세요." : file.name}</span>
        <input
          ref={inputRef}
          accept={SUPPORTED_CONTENT_TYPES.map((type) => `.${type}`).join(",")}
          aria-label="업로드할 파일"
          className="sr-only"
          disabled={disabled}
          id="document-file"
          onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          type="file"
        />
        <span className="mt-2 block text-neutral-500">
          지원 형식: {SUPPORTED_CONTENT_TYPES.join(", ")}
        </span>
      </label>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm text-neutral-400">
          제목 (선택)
          <input
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
            disabled={disabled}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
        </label>
        <label className="text-sm text-neutral-400">
          태그 (쉼표로 구분)
          <input
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
            disabled={disabled}
            onChange={(event) => setTags(event.target.value)}
            value={tags}
          />
        </label>
      </div>

      <fieldset disabled={disabled}>
        <legend className="text-sm text-neutral-400">공개범위</legend>
        <div className="mt-2 flex gap-4 text-sm text-neutral-300">
          <label className="flex items-center gap-2">
            <input
              checked={visibility === "public"}
              name="visibility"
              onChange={() => setVisibility("public")}
              type="radio"
            />
            공개
          </label>
          <label className="flex items-center gap-2">
            <input
              checked={visibility === "private"}
              name="visibility"
              onChange={() => setVisibility("private")}
              type="radio"
            />
            비공개
          </label>
        </div>
      </fieldset>

      {error !== null ? <p className="text-sm text-[#ef4444]">{error}</p> : null}
      {message !== null ? <p className="text-sm text-neutral-400">{message}</p> : null}

      <button
        className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
        disabled={disabled || file === null}
        type="submit"
      >
        {uploading ? "업로드 중…" : "업로드"}
      </button>
    </form>
  );
}
