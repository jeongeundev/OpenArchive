// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import { ApiError, listTrash, purgeDocument, restoreDocument } from "@/lib/api";
import type { TrashItem } from "@/lib/types";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "short",
});

function DateCell({ value }: { value: string }): React.ReactElement {
  return <time dateTime={value}>{DATE_FORMATTER.format(new Date(value))}</time>;
}

export default function TrashPage(): React.ReactElement {
  const [items, setItems] = useState<TrashItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listTrash(controller.signal).then((result) => {
      if (!controller.signal.aborted) setItems(result);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setLoadError(reason instanceof ApiError ? reason.detail : "휴지통을 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, []);

  async function act(item: TrashItem, action: (id: string) => Promise<unknown>, fallback: string): Promise<void> {
    setWorkingId(item.id);
    setActionError(null);
    try {
      await action(item.id);
      setItems((current) => current?.filter((each) => each.id !== item.id) ?? current);
    } catch (reason: unknown) {
      setActionError(reason instanceof ApiError ? reason.detail : fallback);
    } finally {
      setWorkingId(null);
    }
  }

  function purge(item: TrashItem): void {
    if (!window.confirm(`「${item.title}」을(를) 영구 삭제합니다. 삭제하면 되돌릴 수 없습니다.`)) return;
    void act(item, purgeDocument, "문서를 영구 삭제하지 못했습니다.");
  }

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-strong">휴지통</h1>
        <p className="mt-3 text-sm text-muted">
          내가 지운 문서입니다. 복원하면 원래 폴더·열람 범위·태그 그대로 돌아오고, 영구 삭제 예정일이 지나면 자동으로 영구 삭제됩니다.
        </p>
      </div>

      {actionError !== null ? <p className="text-sm text-failed" role="alert">{actionError}</p> : null}

      {loadError !== null ? (
        <p className="text-sm text-muted" role="status">{loadError}</p>
      ) : items === null ? (
        <p className="text-sm text-muted">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted">휴지통이 비어 있습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">제목</th>
                <th className="px-4 py-3 font-medium">삭제 일시</th>
                <th className="px-4 py-3 font-medium">영구 삭제 예정일</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr className="border-b border-line last:border-0" key={item.id}>
                  <td className="px-4 py-3 text-strong">{item.title}</td>
                  <td className="px-4 py-3 text-muted"><DateCell value={item.deleted_at} /></td>
                  <td className="px-4 py-3 text-muted"><DateCell value={item.purge_at} /></td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-3">
                      <button
                        className="text-sm text-accent hover:underline disabled:cursor-not-allowed disabled:text-faint"
                        disabled={workingId !== null}
                        onClick={() => void act(item, restoreDocument, "문서를 복원하지 못했습니다.")}
                        type="button"
                      >
                        복원
                      </button>
                      <button
                        className="text-sm text-muted hover:text-strong disabled:cursor-not-allowed disabled:text-faint"
                        disabled={workingId !== null}
                        onClick={() => purge(item)}
                        type="button"
                      >
                        영구 삭제
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
