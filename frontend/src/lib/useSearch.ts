"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, search } from "./api";
import type { ContentType, SearchResponse } from "./types";

export interface SearchInput {
  query: string;
  tags: string[];
  contentType: ContentType | null;
  folderId: string | null;
  k: number;
}

export function useSearch(): {
  response: SearchResponse | null;
  loading: boolean;
  error: string | null;
  run: (input: SearchInput) => void;
} {
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 마지막 검색만 살린다 — 새 검색이나 화면 이탈이 이전 요청과 백오프 대기를 취소한다.
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = useCallback((input: SearchInput) => {
    if (!input.query.trim()) return;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError(null);
    void search(input, controller.signal)
      .then((nextResponse) => {
        if (!controller.signal.aborted) setResponse(nextResponse);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          reason instanceof ApiError
            ? reason.detail
            : reason instanceof Error
              ? reason.message
              : "검색하지 못했습니다.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }, []);

  return { response, loading, error, run };
}
