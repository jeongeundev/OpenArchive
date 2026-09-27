"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * 사용자 동작으로 시작한 읽기에 넘길 signal을 준다. 컴포넌트가 사라지면 취소되어 응답을
 * 쓸 곳 없는 요청과 백오프 대기가 멈춘다(ADR-048 결정 4). 쓰기에는 넘기지 않는다 — 이미
 * 커밋됐을 수 있다.
 */
export function useUnmountSignal(): () => AbortSignal | undefined {
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    return () => controller.abort();
  }, []);

  return useCallback(() => controllerRef.current?.signal, []);
}
