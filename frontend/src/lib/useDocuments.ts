"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listDocuments } from "./api";
import type { DocumentSummary, EmbeddingStatus } from "./types";

const DEFAULT_INTERVAL_MS = 2_000;

export function useDocuments(params?: {
  status?: EmbeddingStatus;
  intervalMs?: number;
}): {
  documents: DocumentSummary[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const status = params?.status;
  const intervalMs = params?.intervalMs ?? DEFAULT_INTERVAL_MS;
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    void listDocuments(status === undefined ? undefined : { status })
      .then((nextDocuments) => {
        if (!mountedRef.current) return;
        setDocuments(nextDocuments);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current) return;
        setError(reason instanceof Error ? reason.message : "문서를 불러오지 못했습니다.");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (mountedRef.current) setLoading(false);
      });
  }, [status]);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = window.setInterval(refresh, intervalMs);

    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, refresh]);

  return { documents, loading, error, refresh };
}
