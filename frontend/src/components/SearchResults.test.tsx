// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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

/** 결과 카드의 메타 줄 — 형식은 제목 앞 칩, 유사도는 카드 오른쪽에 따로 있다. */
function metaOf(title: string): Element | null | undefined {
  return screen.getByRole("link", { name: title }).closest("article")?.querySelector('[data-testid="result-meta"]');
}

function chipOf(title: string): Element | null | undefined {
  return screen.getByRole("link", { name: title }).closest("article")?.querySelector('[data-testid="result-type"]');
}

describe("SearchResults", () => {
  it("직접 결과 제목을 그 텍스트 버전·그 대목으로 가는 링크로 렌더링하고 점수를 소수점 3자리로 표시한다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute(
      "href",
      "/documents/document-1?version=2&chunk=2",
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

    expect(screen.getByText("검색 결과가 없습니다.")).toHaveClass("text-muted");
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
                from_document_id: "document-1", from_title: "OpenSQL 운영 가이드",
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
      "「OpenSQL 운영 가이드」에서 「관련 있음」로 이어짐",
    );
    expect(screen.getAllByText(/이어짐/)).toHaveLength(1);
  });

  it("함께 볼 문서는 대목이 아니라 문서로 링크한다 — 이전 판 결과의 청크 번호는 그 판의 대목이 아니다", () => {
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
              chunk_index: 4,
              based_on_version: 1,
              via: { from_document_id: "document-1", from_title: "OpenSQL 운영 가이드", kind: "related", depth: 1 },
            },
            {
              ...response.items[0],
              document_id: "document-3",
              title: "OpenSQL 운영 가이드 이전 판",
              chunk_index: 2,
              based_on_version: 1,
              via: { from_document_id: "document-3", from_title: "OpenSQL 운영 가이드 이전 판", kind: "revision", depth: 1 },
            },
          ],
        }}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByRole("link", { name: "OpenProxy 장애 대응" })).toHaveAttribute(
      "href",
      "/documents/document-2",
    );
    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드 이전 판" })).toHaveAttribute(
      "href",
      "/documents/document-3",
    );
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
                from_document_id: "document-1", from_title: "OpenSQL 운영 가이드",
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
      from_document_id: first.document_id, from_title: first.title, kind: "related", depth: 1,
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

  it("응답 목록에 없는 출발 문서도 서버가 준 출처 제목으로 표시한다", () => {
    render(<SearchResults response={{ ...response, items: [{ ...response.items[0],
      via: { from_document_id: "source-document", from_title: "출처 제목", kind: "related", depth: 2 },
    }] }} loading={false} error={null} />);
    expect(screen.getByRole("region", { name: "함께 볼 문서" })).toHaveTextContent("「출처 제목」에서 「관련 있음」로 이어짐");
    expect(screen.queryByText(/연결된 문서/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "검색 결과" })).not.toBeInTheDocument();
  });

  it("확장 결과가 없으면 별도 영역이나 빈 상태를 만들지 않는다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.queryByRole("region", { name: "함께 볼 문서" })).not.toBeInTheDocument();
    expect(screen.queryByText(/이어짐/)).not.toBeInTheDocument();
  });
});

it("긴 발췌의 뒤쪽 근거를 펼쳐 보고 다시 접을 수 있다", () => {
  const evidence = "큰 작업 뒤 작은 문서는 약 22.3초 기다렸습니다.";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], content: "앞부분 설명 ".repeat(80) + evidence }] }} loading={false} error={null} />);
  expect(screen.queryByText(new RegExp(evidence))).not.toBeInTheDocument();
  const expand = screen.getByRole("button", { name: "발췌 더보기" });
  expect(expand).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(expand);
  expect(screen.getByText(new RegExp(evidence))).toBeInTheDocument();
  const collapse = screen.getByRole("button", { name: "발췌 접기" });
  expect(collapse).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(collapse);
  expect(screen.queryByText(new RegExp(evidence))).not.toBeInTheDocument();
});

it("서버가 고른 대목을 미리보기로 쓰고 전체 문맥은 펼쳐서 보여준다", () => {
  const context = "설정 안내 ".repeat(80) + "이전 버전 청크로 계속 검색됩니다.";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], content: context, preview: "이전 버전 청크로 계속 검색됩니다." }] }} loading={false} error={null} />);
  expect(screen.getByText("이전 버전 청크로 계속 검색됩니다.")).toBeInTheDocument();
  expect(screen.queryByText(/설정 안내/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "발췌 더보기" }));
  expect(screen.getByText(/설정 안내/)).toBeInTheDocument();
});

it("첫 발췌 밖의 검색된 본문 대목을 펼쳐 출처와 텍스트 버전을 확인한다", () => {
  const passage = "새 청크가 준비되기 전에는 이전 버전 청크로 검색됩니다.";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0],
    passages: [{ chunk_index: 12, content: passage, based_on_version: 2, score: 0.7 }],
  }] }} loading={false} error={null} />);
  const summary = screen.getByText("검색된 본문 대목 1개");
  const details = summary.closest("details");
  expect(details).not.toHaveAttribute("open");
  expect(screen.getByText(passage)).not.toBeVisible();
  fireEvent.click(summary);
  expect(details).toHaveAttribute("open");
  expect(screen.getByText(passage)).toBeVisible();
  expect(screen.getByText("본문 대목 1 · 텍스트 버전 2")).toBeVisible();
  expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" })).toHaveAttribute("href", "/documents/document-1?version=2&chunk=2");
  fireEvent.click(summary);
  expect(screen.getByText(passage)).not.toBeVisible();
});

it("대목을 임의로 자르거나 HTML로 해석하지 않고 서버의 문맥 순서대로 보여준다", () => {
  const first = "상황 설명 ".repeat(100) + "끝의 근거입니다.";
  const second = "<img src=x onerror=alert(1)> 별도 근거";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], passages: [
    { chunk_index: 12, content: first, based_on_version: 2, score: 0.7 },
    { chunk_index: 30, content: second, based_on_version: 2, score: 0.6 },
  ] }] }} loading={false} error={null} />);
  fireEvent.click(screen.getByText("검색된 본문 대목 2개"));
  const details = screen.getByText("검색된 본문 대목 2개").closest("details");
  expect(details).toHaveTextContent("끝의 근거입니다.");
  expect(screen.getByText(second)).toBeVisible();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(details?.querySelectorAll("section p:first-child")[0]).toHaveTextContent("본문 대목 1 · 텍스트 버전 2");
});

it("추가 대목이 비었으면 불필요한 펼치기를 표시하지 않는다", () => {
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], passages: [] }] }} loading={false} error={null} />);
  expect(screen.queryByText(/검색된 본문 대목/)).not.toBeInTheDocument();
});

it("형식 칩 · 제목 다음 메타를 폴더·소유자·수정일·태그 순서로 두고 유사도는 카드 오른쪽에 흐리게 표시한다", () => {
  const item = { ...response.items[0], content_type: "hwpx" as const, folder_path: ["IT전략실", "통합유지보수"], owner: "IT전략실_김민준", updated_at: "2026-10-26T03:00:00Z" };
  render(<SearchResults response={{ ...response, items: [item] }} loading={false} error={null} />);
  const title = screen.getByRole("link", { name: item.title });
  const chip = chipOf(item.title);
  expect(chip?.textContent).toBe("HWPX");
  expect(chip!.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const meta = metaOf(item.title);
  expect(meta?.textContent).toBe("IT전략실 › 통합유지보수 · IT전략실_김민준 · 2026년 10월 26일 · OpenSQL, 운영");
  expect(meta).toHaveClass("text-xs", "text-muted");
  const score = screen.getByText("유사도 0.877");
  expect(score).toHaveClass("text-xs", "text-muted");
  expect(score).not.toHaveClass("text-accent");
  expect(meta).not.toContainElement(score);
  expect(title.closest("article")).toContainElement(score);
});

it("소유자에 부서가 있으면 `이름(부서)`로 보인다 — 직접 결과와 함께 볼 문서 모두 (D16)", () => {
  const direct = { ...response.items[0], content_type: "hwpx" as const, folder_path: null, owner: "김민준", owner_department: { name: "IT전략실", folder_id: "f1" }, updated_at: "2026-10-26T03:00:00Z" };
  const linked = { ...direct, document_id: "linked", title: "이어진 문서", owner_department: { name: "IT전략실", folder_id: null }, via: { from_document_id: direct.document_id, from_title: direct.title, kind: "related", depth: 1 } };
  render(<SearchResults response={{ ...response, items: [direct, linked] }} loading={false} error={null} />);
  expect(chipOf(direct.title)?.textContent).toBe("HWPX");
  expect(metaOf(direct.title)?.textContent).toBe("김민준(IT전략실) · 2026년 10월 26일 · OpenSQL, 운영");
  expect(chipOf("이어진 문서")?.textContent).toBe("HWPX");
  expect(metaOf("이어진 문서")?.textContent).toBe("김민준(IT전략실) · 2026년 10월 26일 · OpenSQL, 운영");
  expect(screen.getAllByRole("link", { name: "IT전략실" })).toHaveLength(1);
});

it("폴더가 없는 연결 문서는 빈 자리 없이 같은 메타와 연결 경로를 표시한다", () => {
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], content_type: "hwpx", folder_path: null, owner: "IT전략실_김민준", updated_at: "2026-10-26T03:00:00Z", via: { from_document_id: "source", from_title: "출처", kind: "related", depth: 1 } }] }} loading={false} error={null} />);
  expect(chipOf(response.items[0].title)?.textContent).toBe("HWPX");
  expect(metaOf(response.items[0].title)?.textContent).toBe("IT전략실_김민준 · 2026년 10월 26일 · OpenSQL, 운영");
  expect(screen.getByText(/이어짐/)).toHaveTextContent("「출처」에서 「관련 있음」로 이어짐");
});

it("접힌 발췌만 공백을 접어 세 줄로 제한하고 펼치면 줄바꿈을 보존한다", () => {
  const content = "첫 줄\n\n둘째   줄\t설명";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], content }] }} loading={false} error={null} />);
  const excerpt = screen.getByText("첫 줄 둘째 줄 설명");
  expect(excerpt.textContent).toBe("첫 줄 둘째 줄 설명");
  expect(excerpt).toHaveClass("line-clamp-3");
  fireEvent.click(screen.getByRole("button", { name: "발췌 더보기" }));
  expect(excerpt.textContent).toBe(content);
  expect(excerpt).toHaveClass("whitespace-pre-wrap");
  expect(excerpt).not.toHaveClass("line-clamp-3");
});

it("검색된 본문 대목은 원래 공백과 줄바꿈을 보존한다", () => {
  const content = "첫 줄\n\n둘째   줄";
  render(<SearchResults response={{ ...response, items: [{ ...response.items[0], passages: [{ chunk_index: 1, content, based_on_version: 2, score: 0.7 }] }] }} loading={false} error={null} />);
  fireEvent.click(screen.getByText("검색된 본문 대목 1개"));
  const passage = screen.getByText("첫 줄 둘째 줄");
  expect(passage.textContent).toBe(content);
  expect(passage).toHaveClass("whitespace-pre-wrap");
});

describe("검색 결과 배치", () => {
  const linked = {
    ...response.items[0],
    document_id: "document-2",
    title: "OpenProxy 장애 대응",
    content: "연결 문서의 본문 발췌입니다.",
    preview: "연결 문서의 미리보기입니다.",
    folder_path: ["운영"],
    owner: "kim",
    updated_at: "2026-10-26T03:00:00Z",
    via: { from_document_id: "document-1", from_title: "OpenSQL 운영 가이드", kind: "related", depth: 1 },
  };

  it("함께 볼 문서는 검색 결과 아래에 두고, 넓은 화면에서 2열 작은 카드로 놓는다", () => {
    render(<SearchResults response={{ ...response, items: [response.items[0], linked] }} loading={false} error={null} />);

    const direct = screen.getByRole("region", { name: "검색 결과" });
    const related = screen.getByRole("region", { name: "함께 볼 문서" });
    expect(direct.compareDocumentPosition(related) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(direct).not.toContainElement(related);
    expect(screen.getByTestId("related-cards")).toHaveClass("grid", "sm:grid-cols-2");
    expect(within(related).getByRole("link", { name: "OpenProxy 장애 대응" }).closest("article")).toHaveClass("rounded-xl", "border-line", "bg-card");
  });

  it("함께 볼 문서가 없으면 2열 카드 자리를 만들지 않는다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.queryByTestId("related-cards")).not.toBeInTheDocument();
  });

  it("함께 볼 문서 카드 맨 위에 API가 준 관계 라벨을 둔다", () => {
    render(<SearchResults response={{ ...response, items: [response.items[0], linked] }} loading={false} error={null} />);

    const card = within(screen.getByRole("region", { name: "함께 볼 문서" })).getByRole("link", { name: "OpenProxy 장애 대응" }).closest("article");
    expect(card?.firstElementChild).toHaveTextContent(/^관련 있음$/);
  });

  it("직접 결과는 카드로 그린다", () => {
    render(<SearchResults response={response} loading={false} error={null} />);

    expect(screen.getByRole("link", { name: "OpenSQL 운영 가이드" }).closest("article")).toHaveClass("rounded-xl", "border", "border-line", "bg-card");
  });

  it("검색 전에는 무엇을 할 수 있는지 안내한다", () => {
    render(<SearchResults response={null} loading={false} error={null} />);

    expect(screen.getByText("질문이나 키워드로 문서를 찾고, 근거와 함께 답을 받아 보세요.")).toHaveClass("text-muted");
  });

  it("함께 볼 문서는 발췌 없이 형식·제목·메타·연결 이유만 보인다", () => {
    render(<SearchResults response={{ ...response, items: [response.items[0], linked] }} loading={false} error={null} />);

    const related = screen.getByRole("region", { name: "함께 볼 문서" });
    expect(within(related).queryByText(/연결 문서의 미리보기입니다/)).not.toBeInTheDocument();
    expect(within(related).queryByText(/연결 문서의 본문 발췌입니다/)).not.toBeInTheDocument();
    expect(within(related).queryByRole("button", { name: "발췌 더보기" })).not.toBeInTheDocument();
    expect(chipOf("OpenProxy 장애 대응")?.textContent).toBe("MD");
    expect(metaOf("OpenProxy 장애 대응")?.textContent).toBe("운영 · kim · 2026년 10월 26일 · OpenSQL, 운영");
    expect(related).toHaveTextContent("「OpenSQL 운영 가이드」에서 「관련 있음」로 이어짐");
    expect(related).toHaveTextContent("검색 결과에서 관계로 이어진 문서입니다. 각 문서의 연결 이유를 확인하세요.");
  });

  it("실행된 SQL 보기는 검색 결과 목록 아래, 함께 볼 문서 위에 있다", () => {
    render(<SearchResults response={{ ...response, items: [response.items[0], linked] }} loading={false} error={null} />);

    const button = screen.getByRole("button", { name: "실행된 SQL 보기" });
    const direct = screen.getByRole("region", { name: "검색 결과" });
    const related = screen.getByRole("region", { name: "함께 볼 문서" });
    expect(direct.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(direct).not.toContainElement(button);
    expect(button.compareDocumentPosition(related) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
