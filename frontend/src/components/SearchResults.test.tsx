import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchResponse } from "@/lib/types";
import { SearchResults } from "./SearchResults";

const response: SearchResponse = {
  items: [
    {
      document_id: "document-1",
      title: "OpenSQL 운영 가이드",
      tags: ["OpenSQL", "운영"],
      content_type: "md",
      chunk_index: 2,
      content: "OpenProxy 단일 엔드포인트를 통해 데이터베이스에 접속합니다.",
      score: 0.87654,
    },
  ],
  sql: "SELECT actual_sql FROM document_chunks",
};

describe("SearchResults", () => {
  it("결과 제목을 상세 링크로 렌더링하고 점수를 소수점 3자리로 표시한다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/document-1",
    );
    expect(screen.getByText("유사도 0.877")).toBeInTheDocument();
  });

  it("토글을 열면 서버가 반환한 SQL 문자열을 그대로 표시한다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.queryByText(response.sql)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "실행된 SQL 보기" }));
    expect(screen.getByText(response.sql)).toBeInTheDocument();
  });

  it("빈 배열이면 검색 결과 안내를 표시한다", () => {
    render(
      <SearchResults response={{ items: [], sql: "SELECT empty" }} loading={false} error={null} />,
    );

    expect(screen.getByText("검색 결과가 없습니다.")).toHaveClass("text-neutral-500");
  });
});
