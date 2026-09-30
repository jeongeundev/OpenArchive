"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listDocuments } from "./api";
import type { DocumentSummary, EmbeddingStatus } from "./types";

const DEFAULT_INTERVAL_MS = 2_000;

export function useDocuments(params?: {
  status?: EmbeddingStatus;
  limit?: number;
  offset?: number;
  intervalMs?: number;
}): {
  documents: DocumentSummary[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const status = params?.status;
  const limit = params?.limit;
  const offset = params?.offset;
  const intervalMs = params?.intervalMs ?? DEFAULT_INTERVAL_MS;
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 마운트 동안의 요청을 묶는다. 정리할 때 취소해 백오프 대기까지 멈춘다(ADR-048 결정 4).
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;

    inFlightRef.current = true;
    void listDocuments({ status, limit, offset }, controller.signal)
      .then((nextDocuments) => {
        if (controller.signal.aborted) return;
        setDocuments(nextDocuments);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "문서를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [status, limit, offset]);

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

  return { documents, loading, error, refresh };
}
