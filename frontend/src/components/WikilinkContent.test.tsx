import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WikilinkContent } from "./WikilinkContent";

describe("WikilinkContent", () => {
  it("해석된 본문의 위키링크를 문서 상세 링크로 렌더한다", () => {
    render(
      <WikilinkContent
        content="먼저 [[OpenSQL 가이드]]를 읽습니다."
        links={[{ title: "OpenSQL 가이드", document_id: "guide-1" }]}
      />,
    );

    expect(screen.getByRole("link", { name: "OpenSQL 가이드" })).toHaveAttribute(
      "href",
      "/documents/guide-1",
    );
    expect(screen.getByText(/먼저/)).toBeInTheDocument();
  });

  it("깨진 링크는 사유 없이 누를 수 없는 다른 모양으로 렌더한다", () => {
    render(
      <WikilinkContent
        content="[[없는 문서]]와 [[보이지 않는 문서]]"
        links={[
          { title: "없는 문서", document_id: null },
          { title: "보이지 않는 문서", document_id: null },
        ]}
      />,
    );

    expect(screen.queryAllByRole("link")).toHaveLength(0);
    for (const title of ["없는 문서", "보이지 않는 문서"]) {
      const broken = screen.getByText(title);
      expect(broken).toHaveClass("border-dashed", "text-neutral-500");
      expect(broken).not.toHaveAttribute("title");
      expect(broken).not.toHaveAttribute("aria-label");
    }
  });

  it("열람 가능한 동명 문서가 여럿이면 대상을 모두 링크로 보인다", () => {
    render(
      <WikilinkContent
        content="[[같은 제목]]"
        links={[
          { title: "같은 제목", document_id: "first" },
          { title: "같은 제목", document_id: "second" },
        ]}
      />,
    );

    expect(screen.getAllByRole("link", { name: "같은 제목" })).toHaveLength(2);
    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/documents/first",
      "/documents/second",
    ]);
  });

  // 트리거(`011_links_triggers.sql`)가 `btrim(match[1])`으로 저장하므로 API가 주는
  // title에는 양끝 공백이 없다. 화면이 트림하지 않으면 정상 링크가 깨진 링크로 보인다.
  it("양끝 공백이 있는 링크를 트리거와 같은 제목으로 해석한다", () => {
    render(
      <WikilinkContent
        content="[[  OpenSQL 가이드  ]]를 읽습니다."
        links={[{ title: "OpenSQL 가이드", document_id: "guide-1" }]}
      />,
    );

    expect(screen.getByRole("link", { name: "OpenSQL 가이드" })).toHaveAttribute(
      "href",
      "/documents/guide-1",
    );
  });

  // 트리거는 `WHERE btrim(match[1]) <> ''`로 걸러 아예 저장하지 않는다. 화면도
  // 링크로 보지 않아야 양쪽이 같은 것을 링크라고 부른다.
  it("공백뿐인 링크는 링크로 보지 않고 본문 그대로 남긴다", () => {
    render(<WikilinkContent content="앞 [[   ]] 뒤" links={[]} />);

    expect(screen.queryAllByRole("link")).toHaveLength(0);
    // 기본 normalizer가 공백을 축약하므로 원문 보존을 보려면 정규화를 끈다.
    expect(
      screen.getByText("앞 [[   ]] 뒤", { normalizer: (text) => text }),
    ).toBeInTheDocument();
  });

  it("추출 텍스트의 HTML을 실행하지 않고 텍스트로 표시한다", () => {
    const { container } = render(
      <WikilinkContent content={'<script>alert("xss")</script> [[문서]]'} links={[]} />,
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText(/<script>/)).toBeInTheDocument();
  });
});
