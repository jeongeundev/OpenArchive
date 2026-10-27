// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import { ApiError, getDocumentActivity } from "@/lib/api";
import { actionName, describeDetail, viaLabel } from "@/lib/audit";
import type { DocumentActivityEntry } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(reason: unknown): string {
  return reason instanceof ApiError ? reason.detail : "활동 기록을 불러오지 못했습니다.";
}

/** 문서 한 건의 활동 기록. 무엇을 보여 줄지는 서버가 정한다 — 여기서 소유자를 판정하지 않는다 (ADR-055 결정 7). */
export function DocumentActivity({ documentId }: { documentId: string }): React.ReactElement {
  const [items, setItems] = useState<DocumentActivityEntry[] | null>(null);
  const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unmountSignal = useUnmountSignal();

  useEffect(() => {
    const controller = new AbortController();
    getDocumentActivity(documentId, {}, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setItems(page.items);
        setNextBeforeId(page.next_before_id);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [documentId]);

  async function loadMore() {
    if (nextBeforeId === null) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await getDocumentActivity(documentId, { beforeId: nextBeforeId }, unmountSignal());
      setItems(current => [...(current ?? []), ...page.items]);
      setNextBeforeId(page.next_before_id);
    } catch (reason) {
      if (!unmountSignal()?.aborted) setError(errorMessage(reason));
    } finally {
      if (!unmountSignal()?.aborted) setLoadingMore(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-sm font-medium text-muted">활동 기록</h2>
        <p className="text-xs text-muted">
          이 문서에 일어난 변경을 데이터베이스가 작업과 같은 트랜잭션에서 기록합니다. 기록은 고치거나 지울 수 없습니다.
        </p>
      </div>
      {error !== null ? (
        <p className="text-sm text-failed" role="status">
          {error}
        </p>
      ) : null}
      {items !== null && items.length === 0 ? <p className="text-sm text-muted">아직 기록이 없습니다.</p> : null}
      {items !== null && items.length > 0 ? (
        <ol className="space-y-4 border-l border-line pl-4">
          {items.map(item => {
            const via = viaLabel(item.actor_via);
            const description = describeDetail(item);
            return (
              <li key={item.id} className="relative space-y-1">
                <span aria-hidden className="absolute -left-5 top-1.5 h-2 w-2 rounded-full bg-accent" />
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                  <time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time>
                  <span className="text-strong">{item.actor ?? "알 수 없음"}</span>
                  {via !== null ? (
                    <span className="rounded border border-line bg-sunken px-1.5 py-0.5">{via}</span>
                  ) : null}
                </div>
                <p className="text-sm font-medium text-strong">{actionName(item)}</p>
                {description ? <p className="text-sm text-muted">{description}</p> : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      {nextBeforeId !== null ? (
        <button
          type="button"
          className="rounded border border-line px-3 py-1 text-sm text-strong hover:bg-sunken disabled:opacity-50"
          disabled={loadingMore}
          onClick={() => void loadMore()}
        >
          더 보기
        </button>
      ) : null}
    </section>
  );
}
