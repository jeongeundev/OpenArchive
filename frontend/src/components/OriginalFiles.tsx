"use client";

import { useRef, useState } from "react";

import {
  ApiError,
  originalFileUrl,
  originalPreviewUrl,
  isPreviewable,
  reextractDocument,
  replaceOriginalFile,
} from "@/lib/api";
import { MAX_UPLOAD_BYTES, UPLOAD_TOO_LARGE } from "@/lib/limits";
import {
  EXTRACTING_NOTICE,
  SUPPORTED_CONTENT_TYPES,
  type DocumentDetail,
} from "@/lib/types";

// 업로드 상한과 같은 십진 단위로 적는다 — "50MB"가 화면마다 다른 크기를 뜻하지 않게.
function formatSize(bytes: number): string {
  if (bytes < 1_000) return `${bytes}B`;
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)}KB`;
  return `${(bytes / 1_000_000).toFixed(1)}MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium" }).format(new Date(value));
}

function errorMessage(reason: unknown, fallback: string): string {
  if (!(reason instanceof ApiError)) return fallback;
  const serverVersion =
    reason.status === 409 && reason.currentVersion !== undefined
      ? ` 현재 서버 버전: v${reason.currentVersion}`
      : "";
  return `${reason.detail}${serverVersion}`;
}

export function OriginalFiles({
  document,
  disabled,
  anonymous,
  onChanged,
}: {
  document: DocumentDetail;
  disabled: boolean;
  anonymous: boolean;
  onChanged: () => void;
}): React.ReactElement | null {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (anonymous) return null;

  const files = [...document.files].sort((a, b) => b.file_version - a.file_version);
  const latest = files[0]?.file_version;
  const hasOriginal = files.length > 0;
  const extracting = document.extraction_status === "pending";
  const actionsDisabled = disabled || busy || extracting;

  async function replace(file: File): Promise<void> {
    setError(null);
    setNotice(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(UPLOAD_TOO_LARGE);
      return;
    }
    const message = hasOriginal
      ? "새 원본 파일로 교체합니다. 이전 원본은 판 목록에 남고, 새 파일에서 추출한 텍스트가 새 버전이 됩니다."
      : "원본 파일을 올립니다. 파일에서 추출한 텍스트가 새 텍스트 버전이 됩니다.";
    if (!window.confirm(message)) return;

    setBusy(true);
    try {
      await replaceOriginalFile(document.id, file, document.version);
      onChanged();
    } catch (reason: unknown) {
      setError(errorMessage(reason, "원본 파일을 올리지 못했습니다."));
    } finally {
      setBusy(false);
    }
  }

  async function reextract(): Promise<void> {
    setError(null);
    setNotice(null);
    if (
      !window.confirm(
        "최신 원본 파일에서 텍스트를 다시 추출합니다. 추출 텍스트를 직접 고친 내용은 새 버전으로 덮이며, 이전 내용은 버전 이력에서 되돌릴 수 있습니다.",
      )
    ) {
      return;
    }

    setBusy(true);
    try {
      const result = await reextractDocument(document.id, document.version);
      // 원본이 이미지·스캔 PDF면 서버가 텍스트를 쓰지 않고 인식 대기로 넘긴다 — 같다는 뜻이 아니다.
      if (result.changed || result.extraction_status === "pending") {
        onChanged();
      } else {
        setNotice("추출 결과가 현재 텍스트와 같아 새 버전을 만들지 않았습니다.");
      }
    } catch (reason: unknown) {
      setError(errorMessage(reason, "텍스트를 다시 추출하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">원본 파일</h2>

      {hasOriginal ? (
        <ol className="divide-y divide-neutral-800 rounded-lg border border-neutral-800 bg-[#141414] px-5">
          {files.map((file) => (
            <li
              key={file.file_version}
              className="flex flex-wrap items-center justify-between gap-4 py-4"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                <span className="text-neutral-300">{file.file_version}판</span>
                {file.file_version === latest ? (
                  <span className="rounded bg-[#0ea5e9]/10 px-2 py-0.5 text-xs text-[#0ea5e9]">
                    현재
                  </span>
                ) : null}
                <span className="text-neutral-300">{file.filename}</span>
                <span className="text-xs text-neutral-500">{formatSize(file.size)}</span>
                <time className="text-xs text-neutral-500" dateTime={file.uploaded_at}>
                  {formatDate(file.uploaded_at)}
                </time>
                <span className="text-xs text-neutral-500">
                  {file.text_version === null ? "텍스트 인식 전" : `텍스트 v${file.text_version}`}
                </span>
              </div>
              {/* 브라우저가 직접 받게 둔다 — fetch로 받으면 파일 전체가 탭 메모리에 올라간다. */}
              <div className="flex items-center gap-3">
                {isPreviewable(file.filename) ? (
                  <a
                    className="text-xs text-[#0ea5e9] hover:underline"
                    href={originalPreviewUrl(document.id, file.file_version)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    미리보기
                  </a>
                ) : null}
                <a
                  className="text-xs text-[#0ea5e9] hover:underline"
                  download
                  href={originalFileUrl(
                    document.id,
                    file.file_version === latest ? undefined : file.file_version,
                  )}
                >
                  내려받기
                </a>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-neutral-500">원본 파일이 없습니다.</p>
      )}

      <div className="flex flex-wrap gap-3">
        <button
          className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
          disabled={actionsDisabled}
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          {hasOriginal ? "새 파일로 교체" : "원본 파일 올리기"}
        </button>
        {hasOriginal ? (
          <button
            className="text-sm text-neutral-500 hover:text-neutral-300 disabled:cursor-not-allowed disabled:text-neutral-600"
            disabled={actionsDisabled}
            onClick={() => void reextract()}
            type="button"
          >
            원본에서 다시 추출
          </button>
        ) : null}
        <input
          ref={inputRef}
          accept={SUPPORTED_CONTENT_TYPES.map((type) => `.${type}`).join(",")}
          aria-label="새 원본 파일"
          className="sr-only"
          disabled={actionsDisabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            // 같은 파일을 다시 골라도 change가 일어나도록 비운다.
            event.target.value = "";
            if (file !== undefined) void replace(file);
          }}
          type="file"
        />
      </div>

      {extracting ? <p className="text-sm text-neutral-400">{EXTRACTING_NOTICE}</p> : null}

      {error !== null ? (
        <p className="text-sm text-[#f87171]" role="status">
          {error}
        </p>
      ) : null}
      {notice !== null ? (
        <p className="text-sm text-neutral-400" role="status">
          {notice}
        </p>
      ) : null}
    </section>
  );
}
