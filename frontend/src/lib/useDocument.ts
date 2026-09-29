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
  // 마운트 동안의 요청을 묶는다. 정리할 때 취소해 백오프 대기까지 멈춘다(ADR-048 결정 4).
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;

    inFlightRef.current = true;
    void getDocument(id, controller.signal)
      .then((nextDocument) => {
        if (controller.signal.aborted) return;
        setDocument(nextDocument);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
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
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    refresh();

    return () => {
      controller.abort();
      controllerRef.current = null;
      inFlightRef.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    // 텍스트 인식 중에도 폴링한다 — 인식이 끝나면 새로고침 없이 추출 텍스트가 나타난다 (ADR-052).
    if (
      document?.embedding_status !== "pending" &&
      document?.embedding_status !== "processing" &&
      document?.extraction_status !== "pending"
    ) {
      return;
    }

    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [document?.embedding_status, document?.extraction_status, refresh]);

  return { document, loading, error, refresh };
}
