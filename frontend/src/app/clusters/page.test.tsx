import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ClustersPage from "./page";

describe("주제 덩어리 화면", () => {
  it("덩어리를 SVG로 보여주고 클릭하면 문서 목록을 연다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        clusters: [
          {
            name: "검색",
            size: 2,
            documents: [
              { document_id: "doc-1", title: "검색 설계" },
              { document_id: "doc-2", title: "검색 운영" },
            ],
          },
          {
            name: "미분류",
            size: 1,
            documents: [{ document_id: "doc-3", title: "태그 없는 문서" }],
          },
        ],
        connections: [{ source: "검색", target: "미분류", count: 3 }],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    render(<ClustersPage />);

    fireEvent.click(await screen.findByRole("button", { name: "검색 덩어리" }));

    expect(screen.getByRole("heading", { name: "검색 문서 2개" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "검색 설계" })).toHaveAttribute(
      "href",
      "/documents/doc-1",
    );
    expect(screen.getByRole("link", { name: "검색 운영" })).toBeInTheDocument();
  });
});
