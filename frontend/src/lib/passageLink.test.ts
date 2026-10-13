// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import { passageHref } from "./passageLink";

describe("passageHref", () => {
  it("문서 상세의 그 텍스트 버전·그 대목 주소를 만든다", () => {
    expect(passageHref({ document_id: "document-1", based_on_version: 3, chunk_index: 0 })).toBe(
      "/documents/document-1?version=3&chunk=0",
    );
  });

  it("문서 ID를 경로에 안전하게 넣는다", () => {
    expect(passageHref({ document_id: "a/b?c", based_on_version: 1, chunk_index: 2 })).toBe(
      "/documents/a%2Fb%3Fc?version=1&chunk=2",
    );
  });
});
