"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getSystemStatus } from "./api";
import type { SystemStatus } from "./types";

const DEFAULT_INTERVAL_MS = 2_000;

export function useSystemStatus(intervalMs = DEFAULT_INTERVAL_MS): {
  status: SystemStatus | null;
  loading: boolean;
  error: string | null;
} {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;
    inFlightRef.current = true;
    void getSystemStatus(controller.signal)
      .then((nextStatus) => {
        if (controller.signal.aborted) return;
        setStatus(nextStatus);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "상태를 조회하지 못했습니다.");
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
        setLoading(false);
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

  return { status, loading, error };
}
