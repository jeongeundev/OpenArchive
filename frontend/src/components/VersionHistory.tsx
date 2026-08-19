"use client";

import { useState } from "react";

import { ApiError, getDocumentVersion, restoreDocumentVersion } from "@/lib/api";
import type { TextVersion } from "@/lib/types";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function VersionHistory({
  documentId,
  versions,
  currentVersion,
  disabled,
  onRestored,
}: {
  documentId: string;
  versions: TextVersion[];
  currentVersion: number;
  disabled: boolean;
  onRestored: () => void;
}): React.ReactElement {
  const sortedVersions = [...versions].sort((a, b) => b.version - a.version);
  const [openVersion, setOpenVersion] = useState<number | null>(null);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggleText(version: number): Promise<void> {
    setError(null);
    if (openVersion === version) {
      setOpenVersion(null);
      return;
    }
    setOpenVersion(version);
    // 한 번 읽은 버전은 다시 받지 않는다 — 과거 버전의 본문은 바뀌지 않는다.
    if (texts[version] !== undefined) return;
    try {
      const detail = await getDocumentVersion(documentId, version);
      setTexts((previous) => ({ ...previous, [version]: detail.content }));
    } catch (reason: unknown) {
      setOpenVersion(null);
      setError(
        reason instanceof ApiError
          ? reason.detail
          : "텍스트 버전을 불러오지 못했습니다.",
      );
    }
  }

  async function restore(version: number): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await restoreDocumentVersion(documentId, version, currentVersion);
      setConfirming(null);
      onRestored();
    } catch (reason: unknown) {
      if (reason instanceof ApiError) {
        const serverVersion =
          reason.status === 409 && reason.currentVersion !== undefined
            ? ` 현재 서버 버전: v${reason.currentVersion}`
            : "";
        setError(`${reason.detail}${serverVersion}`);
      } else {
        setError("새 텍스트 버전을 만들지 못했습니다.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">텍스트 버전 이력</h2>
      {error !== null ? (
        <p className="text-sm text-[#f87171]" role="status">
          {error}
        </p>
      ) : null}
      {sortedVersions.length === 0 ? (
        <p className="text-sm text-neutral-500">편집 이력이 없습니다.</p>
      ) : (
        <ol className="divide-y divide-neutral-800 rounded-lg border border-neutral-800 bg-[#141414] px-5">
          {sortedVersions.map((item) => (
            <li key={item.version} className="space-y-3 py-4">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-neutral-300">v{item.version}</span>
                  {item.version === currentVersion ? (
                    <span className="rounded bg-[#0ea5e9]/10 px-2 py-0.5 text-xs text-[#0ea5e9]">
                      현재
                    </span>
                  ) : null}
                </div>
                <div className="flex items-center gap-4">
                  <button
                    className="text-xs text-neutral-500 hover:text-neutral-300"
                    onClick={() => void toggleText(item.version)}
                    type="button"
                  >
                    본문 보기
                  </button>
                  {/* 현재 버전에는 되돌리기를 노출하지 않는다 — 이미 그 내용이다. */}
                  {!disabled && item.version !== currentVersion ? (
                    <button
                      className="text-xs text-neutral-500 hover:text-neutral-300"
                      onClick={() => setConfirming(item.version)}
                      type="button"
                    >
                      되돌리기
                    </button>
                  ) : null}
                  <time className="text-xs text-neutral-500" dateTime={item.created_at}>
                    {formatDate(item.created_at)}
                  </time>
                </div>
              </div>

              {openVersion === item.version ? (
                <pre className="whitespace-pre-wrap rounded border border-neutral-800 bg-[#0f0f0f] p-3 text-xs text-neutral-300">
                  {texts[item.version] ?? "불러오는 중…"}
                </pre>
              ) : null}

              {confirming === item.version ? (
                <div className="space-y-2 rounded border border-neutral-800 bg-[#0f0f0f] p-3">
                  {/* 되감기로 오해하지 않도록 새 버전이 생긴다는 것을 밝힌다 (ADR-037). */}
                  <p className="text-xs text-neutral-400">
                    v{item.version}의 내용으로 새 텍스트 버전을 만듭니다. 이력은 지워지지
                    않습니다.
                  </p>
                  <div className="flex items-center gap-3">
                    <button
                      className="text-xs text-[#0ea5e9] hover:underline disabled:cursor-not-allowed disabled:text-neutral-600"
                      disabled={busy}
                      onClick={() => void restore(item.version)}
                      type="button"
                    >
                      새 버전 만들기
                    </button>
                    <button
                      className="text-xs text-neutral-500 hover:text-neutral-300"
                      onClick={() => setConfirming(null)}
                      type="button"
                    >
                      취소
                    </button>
                  </div>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
