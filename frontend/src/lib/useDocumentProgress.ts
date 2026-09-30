"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getDocumentProgress } from "./api";
import type { DocumentProgress } from "./types";

const DEFAULT_INTERVAL_MS = 2_000;

/** 열람 범위 안 문서의 파이프라인 단계별 수. 합이 곧 목록의 전체 수다. */
export function useDocumentProgress(intervalMs = DEFAULT_INTERVAL_MS): {
  progress: DocumentProgress | null;
  refresh: () => void;
} {
  const [progress, setProgress] = useState<DocumentProgress | null>(null);
  // 마운트 동안의 요청을 묶는다. 정리할 때 취소해 백오프 대기까지 멈춘다(ADR-048 결정 4).
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;

    inFlightRef.current = true;
    void getDocumentProgress(controller.signal)
      .then((nextProgress) => {
        if (controller.signal.aborted) return;
        setProgress(nextProgress);
      })
      // 목록 조회가 같은 오류를 화면에 알린다 — 여기서는 마지막 집계를 유지한다.
      .catch(() => undefined)
      .finally(() => {
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
      });
  }, []);

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

  return { progress, refresh };
}
