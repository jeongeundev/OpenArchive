// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT

/** 「그 버전의 그 자리」 — 문서 상세가 이 주소로 그 판을 펼치고 대목을 강조한다. */
export function passageHref(passage: { document_id: string; based_on_version: number; chunk_index: number }): string {
  return `/documents/${encodeURIComponent(passage.document_id)}?version=${passage.based_on_version}&chunk=${passage.chunk_index}`;
}
