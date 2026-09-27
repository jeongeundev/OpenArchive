"use client";

import { useSyncExternalStore } from "react";

import { isRetrying, subscribeRetrying } from "@/lib/api";

/**
 * 읽기 요청이 백오프로 기다리는 동안만 보이는 안내 (ADR-048 결정 4). 무엇이 멈췄는지는
 * 말하지 않는다 — 사용자 화면은 인프라를 드러내지 않는다(UI_GUIDE 원칙 3).
 */
export function RetryNotice(): React.ReactElement | null {
  const retrying = useSyncExternalStore(subscribeRetrying, isRetrying, () => false);
  if (!retrying) return null;
  return (
    <p role="status" className="mb-6 text-sm text-neutral-400">
      연결이 원활하지 않아 다시 시도하는 중입니다.
    </p>
  );
}
