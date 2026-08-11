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

  it("추출 텍스트의 HTML을 실행하지 않고 텍스트로 표시한다", () => {
    const { container } = render(
      <WikilinkContent content={'<script>alert("xss")</script> [[문서]]'} links={[]} />,
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText(/<script>/)).toBeInTheDocument();
  });
});
