"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getDocument } from "./api";
import type { DocumentDetail } from "./types";

const POLL_INTERVAL_MS = 2_000;

export function useDocument(id: string): {
  document: DocumentDetail | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    void getDocument(id)
      .then((nextDocument) => {
        if (!mountedRef.current) return;
        setDocument(nextDocument);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current) return;
        // 422는 경로가 UUID가 아닐 때 FastAPI가 내는 검증 실패다. 사용자에게는 없는
        // 문서와 같은 사실이고, 다르게 보이면 「없는 문서」가 입력 형태에 따라 두 얼굴을
        // 갖는다. 폴백 문구는 상태 코드를 노출하므로 여기서 걸러야 한다.
        setError(
          reason instanceof ApiError && (reason.status === 404 || reason.status === 422)
            ? "문서를 찾을 수 없습니다."
            : reason instanceof Error
              ? reason.message
              : "문서를 불러오지 못했습니다.",
        );
      })
      .finally(() => {
        inFlightRef.current = false;
        if (mountedRef.current) setLoading(false);
      });
  }, [id]);

  useEffect(() => {
    mountedRef.current = true;
    refresh();

    return () => {
      mountedRef.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    if (
      document?.embedding_status !== "pending" &&
      document?.embedding_status !== "processing"
    ) {
      return;
    }

    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [document?.embedding_status, refresh]);

  return { document, loading, error, refresh };
}
