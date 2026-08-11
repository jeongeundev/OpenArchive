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
  broken_links: {
    count: 1,
    items: [{
      source: { document_id: "source-1", title: "링크 출발" },
      target_title: "없는 문서",
    }],
  },
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
    expect(screen.getByText("없는 문서")).toBeInTheDocument();
    expect(screen.getAllByText("정리할 것이 없습니다")).toHaveLength(2);
    expect(screen.queryByText(/오류/)).not.toBeInTheDocument();
    expect(screen.queryByText(/권한 없는/)).not.toBeInTheDocument();
  });

  it("근사 겹침을 같은 내용이라고 단정하지 않는다", async () => {
    // overlaps의 score는 "자기 대목 중 상대 문서에서 최근접 이웃을 찾은 비율"이다.
    // 이웃 판정이 순위 기반이라 주제가 가까운 문서끼리는 1.0에 붙는다 — 실 BGE-M3에서
    // `PRD`↔`UI 디자인 가이드`가 1.00이었다. 화면이 이것을 "같은 내용"으로 부르면
    // 측정이 한계로 남긴 것이 사용자에게는 단정이 된다 (OPENSQL_RESEARCH §14).
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        ...diagnostics,
        duplicates: {
          ...diagnostics.duplicates,
          overlaps: {
            count: 1,
            items: [{
              first: { document_id: "near-1", title: "PRD" },
              second: { document_id: "near-2", title: "UI 디자인 가이드" },
              score: 1.0,
            }],
          },
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    render(<DiagnosticsPage />);

    expect(await screen.findByText("PRD")).toBeInTheDocument();
    expect(screen.getByText("닿은 대목 100%")).toBeInTheDocument();
    expect(screen.getByText(/여러 대목에서 만남/)).toBeInTheDocument();
    expect(screen.queryByText(/겹침 100%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/내용이 겹치는 문서/)).not.toBeInTheDocument();
    expect(screen.queryByText(/같은 내용인지 확인하고/)).not.toBeInTheDocument();
  });
});
