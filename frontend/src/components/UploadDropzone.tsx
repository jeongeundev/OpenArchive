"use client";

import { useRef, useState } from "react";

import { ApiError, uploadDocument } from "@/lib/api";
import { SUPPORTED_CONTENT_TYPES, type Visibility } from "@/lib/types";
import { expandZip } from "@/lib/zip";

// 백엔드 경계(backend/app/api/documents.py의 MAX_UPLOAD_BYTES)와 같은 값·문구.
// 선검사는 대형 파일의 전송 비용을 아끼기 위한 것이고, 경계의 최종 권위는 백엔드의 413이다.
const MAX_UPLOAD_BYTES = 10_000_000;
const UPLOAD_TOO_LARGE = "업로드 파일은 10MB를 넘을 수 없습니다.";

type UploadItemStatus = "대기" | "업로드 중" | "완료" | "실패" | "건너뜀";

type UploadItem = {
  file?: File;
  name: string;
  status: UploadItemStatus;
  detail?: string;
};

export function UploadDropzone({
  onUploaded,
}: {
  onUploaded: () => void;
}): React.ReactElement {
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fileCount = items.filter((item) => item.file !== undefined).length;
  const pendingCount = items.filter((item) => item.status === "대기").length;

  async function selectFiles(files: File[]): Promise<void> {
    const selected: UploadItem[] = [];
    try {
      for (const file of files) {
        if (file.name.toLowerCase().endsWith(".zip")) {
          const expanded = await expandZip(file);
          selected.push(
            ...expanded.files.map((entry) => ({
              file: entry,
              name: entry.name,
              status: "대기" as const,
            })),
            ...expanded.skipped.map((name) => ({ name, status: "건너뜀" as const })),
          );
        } else {
          selected.push({ file, name: file.name, status: "대기" });
        }
      }
    } catch {
      setItems([]);
      setMessage(null);
      setError("ZIP 파일을 열 수 없습니다.");
      return;
    }
    setItems(selected);
    setMessage(null);
    setError(null);
  }

  function patchItem(index: number, patch: Partial<UploadItem>): void {
    setItems((current) =>
      current.map((item, at) => (at === index ? { ...item, ...patch } : item)),
    );
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (pendingCount === 0 || uploading) return;

    setUploading(true);
    setMessage(null);
    setError(null);
    const cleanTitle = title.trim();
    const cleanTags = tags.split(",").map((tag) => tag.trim()).filter(Boolean);
    let succeeded = 0;
    let failed = 0;
    for (const [index, item] of items.entries()) {
      if (item.status !== "대기" || item.file === undefined) continue;
      patchItem(index, { status: "업로드 중" });
      if (item.file.size > MAX_UPLOAD_BYTES) {
        patchItem(index, { status: "실패", detail: UPLOAD_TOO_LARGE });
        failed += 1;
        continue;
      }
      try {
        await uploadDocument({
          file: item.file,
          // 배치에는 제목이 파일명이다 — 파일이 1개일 때만 입력한 제목을 보낸다.
          title: fileCount === 1 && cleanTitle !== "" ? cleanTitle : undefined,
          tags: cleanTags,
          visibility,
        });
        patchItem(index, { status: "완료" });
        succeeded += 1;
      } catch (reason: unknown) {
        patchItem(index, {
          status: "실패",
          detail: reason instanceof ApiError ? reason.detail : "업로드에 실패했습니다.",
        });
        failed += 1;
      }
    }
    if (failed === 0) {
      setTitle("");
      setTags("");
      setVisibility("public");
      setMessage("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다.");
    } else {
      setError(succeeded > 0 ? "일부 파일을 업로드하지 못했습니다." : "업로드에 실패했습니다.");
    }
    if (inputRef.current !== null) inputRef.current.value = "";
    if (succeeded > 0) onUploaded();
    setUploading(false);
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
          if (!disabled) void selectFiles(Array.from(event.dataTransfer.files));
        }}
      >
        <span>
          {fileCount === 0
            ? "파일을 끌어놓거나 클릭해 선택하세요."
            : `${fileCount}개 파일 선택됨`}
        </span>
        <input
          ref={inputRef}
          accept={`${SUPPORTED_CONTENT_TYPES.map((type) => `.${type}`).join(",")},.zip`}
          aria-label="업로드할 파일"
          className="sr-only"
          disabled={disabled}
          id="document-file"
          multiple
          onChange={(event) => void selectFiles(Array.from(event.target.files ?? []))}
          type="file"
        />
        <span className="mt-2 block text-neutral-500">
          지원 형식: {SUPPORTED_CONTENT_TYPES.join(", ")} · ZIP 안의 지원 문서도 선택할 수 있습니다.
        </span>
      </label>

      {items.length > 0 ? (
        <ul aria-label="업로드 목록" className="space-y-1 text-sm text-neutral-300">
          {items.map((item, index) => (
            <li key={`${item.name}-${index}`} className="flex flex-wrap gap-x-2">
              <span>{`${item.name} — ${item.status}`}</span>
              {item.detail !== undefined ? (
                <span className="text-[#ef4444]">{item.detail}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        {fileCount <= 1 ? (
          <label className="text-sm text-neutral-400">
            제목 (선택)
            <input
              className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
              disabled={disabled}
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
          </label>
        ) : null}
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
        disabled={disabled || pendingCount === 0}
        type="submit"
      >
        {uploading ? "업로드 중…" : "업로드"}
      </button>
    </form>
  );
}
