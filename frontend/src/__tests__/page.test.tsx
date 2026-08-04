import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "@/app/page";

describe("루트 페이지", () => {
  it("프로젝트 이름을 표시한다", () => {
    render(<Home />);

    expect(screen.getByRole("heading", { name: "OpenArchive" })).toBeInTheDocument();
  });
});
