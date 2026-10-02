import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchResponse } from "@/lib/types";
import { SearchResults } from "./SearchResults";

const response: SearchResponse = {
  items: [
    {
      document_id: "document-1",
      title: "OpenSQL 운영 가이드",
      filename: "opensql.md",
      tags: ["OpenSQL", "운영"],
      content_type: "md",
      chunk_index: 2,
      content: "OpenProxy 단일 엔드포인트를 통해 데이터베이스에 접속합니다.",
      score: 0.87654,
      based_on_version: 2,
      via: null,
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

  it("확장 결과에는 출발 문서와 도달 관계를 표시하고 직접 매칭에는 표시하지 않는다", () => {
    render(
      <SearchResults
        response={{
          ...response,
          items: [
            response.items[0],
            {
              ...response.items[0],
              document_id: "document-2",
              title: "OpenProxy 장애 대응",
              via: {
                from_document_id: "document-1",
                kind: "related",
                depth: 1,
              },
            },
          ],
        }}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByText(/이어짐/)).toHaveTextContent(
      "OpenSQL 운영 가이드에서 「관련 있음」로 이어짐",
    );
    expect(screen.getAllByText(/이어짐/)).toHaveLength(1);
  });

  it("확장 결과에는 유사도를 표시하지 않는다", () => {
    render(
      <SearchResults
        response={{
          ...response,
          items: [
            response.items[0],
            {
              ...response.items[0],
              document_id: "document-2",
              title: "OpenProxy 장애 대응",
              // 확장 결과의 score는 진입점 거리에 단계 페널티를 더한 값이라
              // 질의와의 유사도가 아니다. 음수가 그대로 노출되면 안 된다.
              score: -1.8596,
              via: {
                from_document_id: "document-1",
                kind: "related",
                depth: 1,
              },
            },
          ],
        }}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByText("유사도 0.877")).toBeInTheDocument();
    expect(screen.getAllByText(/유사도/)).toHaveLength(1);
    expect(screen.queryByText(/-1\.860/)).not.toBeInTheDocument();
  });

  it("직접 결과의 순서를 유지하고 연결 문서는 별도 영역에 한 번씩 표시한다", () => {
    const first = response.items[0];
    const linked = { ...first, document_id: "document-2", title: "장애 대응", via: {
      from_document_id: first.document_id, kind: "related", depth: 1,
    } };
    const second = { ...first, document_id: "document-3", title: "백업 가이드" };
    render(<SearchResults response={{ ...response, items: [first, second, linked,
      { ...first, via: linked.via }, { ...linked, chunk_index: 3 },
    ] }} loading={false} error={null} />);

    const direct = screen.getByRole("region", { name: "검색 결과" });
    const related = screen.getByRole("region", { name: "함께 볼 문서" });
    expect(within(direct).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "OpenSQL 운영 가이드", "백업 가이드",
    ]);
    expect(within(related).getAllByRole("link")).toHaveLength(1);
    expect(within(related).getByRole("link", { name: "장애 대응" })).toBeInTheDocument();
    expect(within(related).queryByRole("link", { name: first.title })).not.toBeInTheDocument();
  });

  it("응답에 없는 출발 문서는 제목이나 링크를 추측해서 노출하지 않는다", () => {
    render(<SearchResults response={{ ...response, items: [{ ...response.items[0],
      via: { from_document_id: "hidden-document", kind: "refers", depth: 2 },
    }] }} loading={false} error={null} />);
    expect(screen.getByRole("region", { name: "함께 볼 문서" })).toHaveTextContent("연결된 문서에서");
    expect(screen.queryByRole("link", { name: "hidden-document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "검색 결과" })).not.toBeInTheDocument();
  });

  it("확장 결과가 없으면 별도 영역이나 빈 상태를 만들지 않는다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.queryByRole("region", { name: "함께 볼 문서" })).not.toBeInTheDocument();
    expect(screen.queryByText(/이어짐/)).not.toBeInTheDocument();
  });
});
