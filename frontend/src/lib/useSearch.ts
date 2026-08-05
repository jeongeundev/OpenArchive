"use client";

import { useCallback, useState } from "react";

import { ApiError, search } from "./api";
import type { ContentType, SearchResponse } from "./types";

export interface SearchInput {
  query: string;
  tags: string[];
  contentType: ContentType | null;
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

  const run = useCallback((input: SearchInput) => {
    if (!input.query.trim()) return;

    setLoading(true);
    setError(null);
    void search(input)
      .then((nextResponse) => setResponse(nextResponse))
      .catch((reason: unknown) => {
        setError(
          reason instanceof ApiError
            ? reason.detail
            : reason instanceof Error
              ? reason.message
              : "검색하지 못했습니다.",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  return { response, loading, error, run };
}
