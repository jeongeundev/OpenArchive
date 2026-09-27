"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getRelated, getTagSuggestions } from "./api";
import type { RelatedResponse, TagSuggestionsResponse } from "./types";

export function useRelated(
  id: string,
  chunkVersion: number | null,
): {
  related: RelatedResponse | null;
  suggestions: TagSuggestionsResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [related, setRelated] = useState<RelatedResponse | null>(null);
  const [suggestions, setSuggestions] = useState<TagSuggestionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const controller = controllerRef.current;
    if (controller === null || inFlightRef.current) return;

    inFlightRef.current = true;
    void Promise.all([getRelated(id, controller.signal), getTagSuggestions(id, controller.signal)])
      .then(([nextRelated, nextSuggestions]) => {
        if (controller.signal.aborted) return;
        setRelated(nextRelated);
        setSuggestions(nextSuggestions);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "관련 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [id]);

  // 문서나 청크 버전이 바뀌면 이전 조회를 취소하고 새 기준으로 다시 받는다.
  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    refresh();
    return () => {
      controller.abort();
      controllerRef.current = null;
      inFlightRef.current = false;
    };
  }, [refresh, chunkVersion]);

  return { related, suggestions, loading, error, refresh };
}
