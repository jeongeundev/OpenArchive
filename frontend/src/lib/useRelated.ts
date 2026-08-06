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
  const mountedRef = useRef(false);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    void Promise.all([getRelated(id), getTagSuggestions(id)])
      .then(([nextRelated, nextSuggestions]) => {
        if (!mountedRef.current) return;
        setRelated(nextRelated);
        setSuggestions(nextSuggestions);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current) return;
        setError(reason instanceof Error ? reason.message : "관련 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (mountedRef.current) setLoading(false);
      });
  }, [id]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, chunkVersion]);

  return { related, suggestions, loading, error, refresh };
}
