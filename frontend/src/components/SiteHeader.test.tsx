import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SiteHeader } from "./SiteHeader";

describe("SiteHeader", () => {
  it("문서와 검색 화면으로 이동하는 링크를 제공한다", () => {
    render(<SiteHeader />);

    expect(screen.getByRole("link", { name: "문서" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "검색" })).toHaveAttribute("href", "/search");
  });

  it("운영 화면 링크를 사용자 내비게이션에 노출하지 않는다", () => {
    const { container } = render(<SiteHeader />);

    expect(container.innerHTML).not.toContain("/" + "admin");
  });
});
