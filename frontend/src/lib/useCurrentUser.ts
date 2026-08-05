"use client";

import { useSyncExternalStore } from "react";

import { getCurrentUser } from "./user";

// 데모 사용자는 헤더에서 바꿀 때 window.location.reload()로 화면을 통째로 다시 그린다.
// 구독할 변경 이벤트가 없으므로 해지 함수만 돌려준다.
function subscribe(): () => void {
  return () => {};
}

/**
 * 저장된 데모 사용자. 서버(프리렌더)에서는 항상 null이다.
 *
 * 렌더 중에 localStorage를 직접 읽으면, 프리렌더된 HTML(localStorage가 없으니 항상 익명)과
 * 첫 클라이언트 렌더가 어긋나 하이드레이션이 깨진다. 서버 스냅샷을 null로 고정해 첫 렌더를
 * 맞추고, 하이드레이션 직후 저장된 값으로 다시 그린다.
 */
export function useCurrentUser(): string | null {
  return useSyncExternalStore(subscribe, getCurrentUser, () => null);
}
