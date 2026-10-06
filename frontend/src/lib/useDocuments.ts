"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { countDocuments, listDocuments, type DocumentFilters } from "./api";
import type { DocumentSummary } from "./types";

const DEFAULT_INTERVAL_MS = 2_000;

export function useDocuments(params?: DocumentFilters & {
  limit?: number;
  offset?: number;
  intervalMs?: number;
}): {
  documents: DocumentSummary[];
  total: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const folderId = params?.folderId;
  const status = params?.status;
  const q = params?.q;
  const contentType = params?.contentType;
  const tag = params?.tag;
  const sort = params?.sort;
  const limit = params?.limit;
  const offset = params?.offset;
  const intervalMs = params?.intervalMs ?? DEFAULT_INTERVAL_MS;
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 마운트 동안의 요청을 묶는다. 정리할 때 취소해 백오프 대기까지 멈춘다(ADR-048 결정 4).
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;

    inFlightRef.current = true;
    const filters = { folderId, status, q, contentType, tag };
    void Promise.allSettled([
      listDocuments({ ...filters, sort, limit, offset }, controller.signal),
      countDocuments(filters, controller.signal),
    ])
      .then(([listResult, countResult]) => {
        if (controller.signal.aborted) return;
        if (listResult.status === "fulfilled") setDocuments(listResult.value);
        setTotal(countResult.status === "fulfilled" ? countResult.value : null);
        const failure = listResult.status === "rejected" ? listResult : countResult.status === "rejected" ? countResult : null;
        setError(failure === null ? null : failure.reason instanceof Error ? failure.reason.message : "문서를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [folderId, status, q, contentType, tag, sort, limit, offset]);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    refresh();
    const timer = window.setInterval(refresh, intervalMs);

    return () => {
      controller.abort();
      controllerRef.current = null;
      inFlightRef.current = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, refresh]);

  return { documents, total, loading, error, refresh };
}
