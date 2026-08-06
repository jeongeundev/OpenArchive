import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { updateTags } from "@/lib/api";
import { TagSuggestions } from "./TagSuggestions";

vi.mock("@/lib/api", () => ({ updateTags: vi.fn() }));

describe("TagSuggestions", () => {
  beforeEach(() => {
    vi.mocked(updateTags).mockReset();
  });

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

  it("추천 태그 클릭 시 전체 태그를 저장하고 문서와 추천을 갱신한다", async () => {
    vi.mocked(updateTags).mockResolvedValue({} as never);
    const refreshDocument = vi.fn();
    const refreshSuggestions = vi.fn();

    render(
      <TagSuggestions
        onApply={async (tag) => {
          await updateTags("document-1", ["OpenSQL", tag]);
          refreshDocument();
          refreshSuggestions();
        }}
        response={{
          items: [{ tag: "pgvector", freq: 2 }],
          based_on_version: 2,
          reason: null,
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "pgvector 태그 적용" }));

    await waitFor(() => {
      expect(updateTags).toHaveBeenCalledWith("document-1", ["OpenSQL", "pgvector"]);
    });
    expect(refreshDocument).toHaveBeenCalledOnce();
    expect(refreshSuggestions).toHaveBeenCalledOnce();
  });
});
