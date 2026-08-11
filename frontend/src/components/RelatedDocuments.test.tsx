import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RelatedResponse } from "@/lib/types";
import { RelatedDocuments } from "./RelatedDocuments";

const related: RelatedResponse = {
  items: [
    {
      document_id: "related-1",
      title: "OpenSQL 운영 가이드",
      tags: ["OpenSQL", "운영"],
      kind: "related",
      score: 0.8123,
    },
  ],
  identical: [],
  based_on_version: 3,
  reason: null,
};

describe("RelatedDocuments", () => {
  it("관련 문서를 점수와 기준 버전, 상세 링크와 함께 표시한다", () => {
    render(<RelatedDocuments response={related} />);

    expect(screen.getByText("내용이 유사한 문서가 있습니다")).toBeInTheDocument();
    expect(screen.getByText("0.812")).toBeInTheDocument();
    expect(screen.getByText("v3 기준")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/related-1",
    );
    expect(screen.queryByText(/중복/)).not.toBeInTheDocument();
  });

  it("색인 전에는 안내만 표시한다", () => {
    render(
      <RelatedDocuments
        response={{ items: [], identical: [], based_on_version: null, reason: "not_indexed" }}
      />,
    );

    expect(screen.getByText("임베딩이 완료되면 표시됩니다.")).toHaveClass(
      "text-neutral-500",
    );
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("동일한 텍스트 문서를 별도 링크로 표시한다", () => {
    render(
      <RelatedDocuments
        response={{
          ...related,
          identical: [{ document_id: "same-1", title: "같은 운영 가이드" }],
        }}
      />,
    );

    expect(screen.getByText("동일한 텍스트의 문서가 있습니다")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "같은 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/same-1",
    );
    expect(screen.queryByText(/중복/)).not.toBeInTheDocument();
  });

  it("색인됐지만 이웃이 없으면 빈 상태를 표시한다", () => {
    render(
      <RelatedDocuments
        response={{ items: [], identical: [], based_on_version: 1, reason: "no_edges" }}
      />,
    );

    expect(screen.getByText("처리는 끝났지만 관련 문서가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("임베딩이 완료되면 표시됩니다.")).not.toBeInTheDocument();
  });

  it("관계 종류별로 묶고 각 그룹 안에서만 점수순으로 정렬한다", () => {
    render(
      <RelatedDocuments
        response={{
          ...related,
          items: [
            { ...related.items[0], document_id: "related-low", title: "관련 낮음", score: 0.2 },
            {
              ...related.items[0],
              document_id: "overlap-low",
              title: "겹침 낮음",
              kind: "overlaps",
              score: 0.1,
            },
            { ...related.items[0], document_id: "related-high", title: "관련 높음", score: 0.9 },
            {
              ...related.items[0],
              document_id: "overlap-high",
              title: "겹침 높음",
              kind: "overlaps",
              score: 0.8,
            },
          ],
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "여러 대목에서 만난다" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "관련 있음" })).toBeInTheDocument();
    const overlapGroup = screen.getByRole("heading", { name: "여러 대목에서 만난다" })
      .parentElement;
    const relatedGroup = screen.getByRole("heading", { name: "관련 있음" }).parentElement;
    expect(overlapGroup).not.toBeNull();
    expect(relatedGroup).not.toBeNull();
    expect(within(overlapGroup!).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "겹침 높음",
      "겹침 낮음",
    ]);
    expect(within(relatedGroup!).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "관련 높음",
      "관련 낮음",
    ]);
  });
});
