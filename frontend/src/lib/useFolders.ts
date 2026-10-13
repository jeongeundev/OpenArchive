// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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
  // 조회 중에 받은 refresh는 버리지 않고 끝난 뒤 한 번 더 조회한다 — 버리면 생성 직후 새 폴더가 옛 응답에 가린다.
  const pendingRef = useRef(false);
  const refresh = useCallback(function run() {
    const controller = controllerRef.current;
    if (!controller) return;
    if (inFlightRef.current) {
      pendingRef.current = true;
      return;
    }
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
      if (pendingRef.current) {
        pendingRef.current = false;
        run();
        return;
      }
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
      pendingRef.current = false;
    };
  }, [enabled, refresh]);

  return { folders: enabled ? folders : [], loading: enabled && loading, error: enabled ? error : null, refresh };
}
