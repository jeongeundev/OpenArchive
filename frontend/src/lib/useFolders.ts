"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { listFolders } from "./api";
import type { Folder } from "./types";

export function useFolders(enabled = true) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (!controller || inFlightRef.current) return;
    inFlightRef.current = true;
    setLoading(true);
    void listFolders(controller.signal).then(list => {
      if (controller.signal.aborted) return;
      setFolders(list);
      setError(null);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "폴더를 불러오지 못했습니다.");
    }).finally(() => {
      if (controller.signal.aborted) return;
      inFlightRef.current = false;
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    refresh();
    return () => {
      controller.abort();
      controllerRef.current = null;
      inFlightRef.current = false;
    };
  }, [enabled, refresh]);

  return { folders: enabled ? folders : [], loading: enabled && loading, error: enabled ? error : null, refresh };
}
