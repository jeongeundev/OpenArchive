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

  it("크기가 비슷한 덩어리도 가장 작은 것과 가장 큰 것의 차이가 보이게 그린다", async () => {
    // #89 ② 실측 모양 — 최대값 기준 정규화로는 굵기가 5.7·5.8·6.0, 원이 거의 같았다.
    const cluster = (name: string, size: number) => ({ name, size, documents: [] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        clusters: [cluster("조사", 24), cluster("adr", 21), cluster("문서관리", 20)],
        connections: [
          { source: "조사", target: "adr", count: 88 },
          { source: "adr", target: "문서관리", count: 91 },
          { source: "조사", target: "문서관리", count: 94 },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    render(<ClustersPage />);

    const radius = async (name: string) =>
      Number((await screen.findByRole("button", { name: `${name} 덩어리` })).querySelector("circle")!.getAttribute("r"));
    expect(await radius("문서관리")).toBe(22);
    expect(await radius("조사")).toBe(44);
    const widths = Array.from(document.querySelectorAll("line")).map((line) => Number(line.getAttribute("stroke-width")));
    expect(Math.min(...widths)).toBe(1);
    expect(Math.max(...widths)).toBe(6);
  });
});

// 응답하지 않는 서버 — 화면을 떠날 때 요청이 취소되는지만 본다.
function pendingFetch() {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}));
}

describe("관계 지도 화면 취소", () => {
  it("화면을 떠나면 진행 중인 조회를 취소한다", () => {
    const fetchMock = pendingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<ClustersPage />);
    unmount();

    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});
