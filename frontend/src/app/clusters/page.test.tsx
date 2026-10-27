// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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

function stubClusters(clusters: { name: string; size: number }[]) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify({
      clusters: clusters.map((cluster) => ({ ...cluster, documents: [] })),
      connections: [],
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));
}

function circleOf(group: HTMLElement) {
  const circle = group.querySelector("circle")!;
  return {
    cx: Number(circle.getAttribute("cx")),
    cy: Number(circle.getAttribute("cy")),
    r: Number(circle.getAttribute("r")),
  };
}

describe("관계 지도 배치", () => {
  it("덩어리 이름은 원 아래에, 문서 수는 원 안에 그린다", async () => {
    stubClusters([{ name: "검색", size: 3 }, { name: "운영", size: 1 }, { name: "보안", size: 2 }]);

    render(<ClustersPage />);

    for (const name of ["검색", "운영", "보안"]) {
      const group = await screen.findByRole("button", { name: `${name} 덩어리` });
      const { cy, r } = circleOf(group);
      const label = Array.from(group.querySelectorAll("text")).find((text) => text.textContent === name)!;
      expect(Number(label.getAttribute("y"))).toBeGreaterThan(cy + r);
      const count = Array.from(group.querySelectorAll("text")).find((text) => text.textContent?.endsWith("개"))!;
      const countY = Number(count.getAttribute("y"));
      expect(countY).toBeGreaterThanOrEqual(cy - r);
      expect(countY).toBeLessThanOrEqual(cy + r);
    }
  });

  it("이름이 24자를 넘으면 24자와 「…」로 줄이고 전체 이름은 title과 aria-label에 남긴다", async () => {
    const longName = "가나다라마바사아자차카타파하가나다라마바사아자차카타";
    stubClusters([{ name: longName, size: 2 }, { name: "짧은 이름", size: 1 }]);

    render(<ClustersPage />);

    const group = await screen.findByRole("button", { name: `${longName} 덩어리` });
    const texts = Array.from(group.querySelectorAll("text")).map((text) => text.textContent);
    expect(texts).toContain(`${longName.slice(0, 24)}…`);
    expect(texts).not.toContain(longName);
    expect(group.querySelector("title")?.textContent).toBe(longName);
  });

  it("덩어리가 2개면 좌우로 놓는다", async () => {
    stubClusters([{ name: "왼쪽", size: 2 }, { name: "오른쪽", size: 1 }]);

    render(<ClustersPage />);

    const first = circleOf(await screen.findByRole("button", { name: "왼쪽 덩어리" }));
    const second = circleOf(screen.getByRole("button", { name: "오른쪽 덩어리" }));
    expect(first.cy).toBe(second.cy);
    expect(first.cx).not.toBe(second.cx);
  });

  it("그래프는 폭을 채우되 높이 상한을 둔다", async () => {
    stubClusters([{ name: "검색", size: 2 }, { name: "운영", size: 1 }]);

    render(<ClustersPage />);

    const svg = await screen.findByRole("img", { name: "문서 덩어리와 덩어리 사이 연결" });
    expect(svg).toHaveClass("w-full");
    expect(svg).toHaveClass("max-h-[560px]");
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
