import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TagSuggestions } from "./TagSuggestions";

describe("TagSuggestions", () => {
  it("추천 태그를 읽기 전용 칩과 빈도로 표시한다", () => {
    render(
      <TagSuggestions
        response={{
          items: [{ tag: "OpenSQL", freq: 3 }],
          based_on_version: 2,
          reason: null,
        }}
      />,
    );

    expect(screen.getByText("OpenSQL")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("색인 전에는 안내만 표시한다", () => {
    render(
      <TagSuggestions response={{ items: [], based_on_version: null, reason: "not_indexed" }} />,
    );

    expect(screen.getByText("임베딩이 완료되면 표시됩니다.")).toHaveClass(
      "text-neutral-500",
    );
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("색인됐지만 추천이 없으면 빈 상태를 표시한다", () => {
    render(
      <TagSuggestions response={{ items: [], based_on_version: 1, reason: null }} />,
    );

    expect(screen.getByText("추천할 태그가 없습니다.")).toBeInTheDocument();
  });
});
