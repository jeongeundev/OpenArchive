import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ClustersPage from "./page";

describe("관계 지도 화면", () => {
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

    expect(screen.getByRole("heading", { level: 1, name: "관계 지도" })).toBeInTheDocument();
    expect(screen.queryByText(/태그로 묶/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/묶음은 관계로 계산한 추천이며 사실처럼 단정하지 않습니다\./),
    ).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "검색 덩어리" }));

    expect(screen.getByRole("heading", { name: "검색 문서 2개" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "검색 설계" })).toHaveAttribute(
      "href",
      "/documents/doc-1",
    );
    expect(screen.getByRole("link", { name: "검색 운영" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "미분류 덩어리" }));
    expect(
      screen.getByText("아직 관계가 계산되지 않았거나 이어진 문서가 없는 문서입니다."),
    ).toBeInTheDocument();
  });
});
