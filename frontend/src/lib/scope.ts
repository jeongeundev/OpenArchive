// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { VISIBILITY_LABEL, type DocumentSummary } from "@/lib/types";

// 목록·상세가 같은 문구를 쓴다. 부여 대상 이름은 넣지 않는다(#288 D3).
export function scopeSummary(
  document: Pick<DocumentSummary, "effective_visibility" | "follows_folder">,
): string {
  const label = VISIBILITY_LABEL[document.effective_visibility];
  return document.follows_folder ? `${label} · 폴더 범위 따름` : label;
}
