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
  const mountedRef = useRef(false);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    void getSystemStatus()
      .then((nextStatus) => {
        if (!mountedRef.current) return;
        setStatus(nextStatus);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current) return;
        setError(reason instanceof Error ? reason.message : "상태를 조회하지 못했습니다.");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, refresh]);

  return { status, loading, error };
}
