import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DiagnosticsPage from "./page";

const diagnostics = {
  orphans: { count: 1, items: [{ document_id: "orphan-1", title: "외톨이 문서" }] },
  duplicates: {
    identical: {
      count: 1,
      items: [{
        first: { document_id: "same-1", title: "동일 A" },
        second: { document_id: "same-2", title: "동일 B" },
        score: null,
      }],
    },
    overlaps: { count: 0, items: [] },
  },
  uncategorized: { count: 0, items: [] },
};

describe("문서 진단 화면", () => {
  it("고아·중복·미분류를 행동 안내와 제한된 목록으로 보여준다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify(diagnostics),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    render(<DiagnosticsPage />);

    expect(await screen.findByText("외톨이 문서")).toBeInTheDocument();
    expect(screen.getByText(/태그를 달거나 다른 문서에서 참조해 보세요/)).toBeInTheDocument();
    expect(screen.getByText("동일 A")).toBeInTheDocument();
    expect(screen.getByText("동일 B")).toBeInTheDocument();
    expect(screen.getAllByText("정리할 것이 없습니다")).toHaveLength(2);
    expect(screen.queryByText(/오류/)).not.toBeInTheDocument();
    expect(screen.queryByText(/권한 없는/)).not.toBeInTheDocument();
  });
});
