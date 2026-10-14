// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ask } from "./api";
import type { AskResponse } from "./types";
import type { SearchInput } from "./useSearch";

/** 근거 기반 답변 요청. 마지막 요청만 살린다 — 새 검색은 화면을 새로 그려 이전 요청을 취소한다. */
export function useAsk(): {
  response: AskResponse | null;
  loading: boolean;
  error: string | null;
  run: (input: SearchInput) => void;
} {
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = useCallback((input: SearchInput) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setResponse(null);
    setLoading(true);
    setError(null);
    void ask(input, controller.signal)
      .then((nextResponse) => {
        if (!controller.signal.aborted) setResponse(nextResponse);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof ApiError ? reason.detail : "답변을 받지 못했습니다.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }, []);

  return { response, loading, error, run };
}
