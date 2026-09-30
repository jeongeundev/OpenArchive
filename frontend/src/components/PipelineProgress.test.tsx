import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentProgress } from "@/lib/types";
import { PipelineProgress } from "./PipelineProgress";

const idle: DocumentProgress = {
  extracting: 0,
  extraction_failed: 0,
  pending: 0,
  processing: 0,
  ready: 0,
  error: 0,
};

describe("PipelineProgress", () => {
  it("전체 수와 문서가 있는 단계만 보인다", () => {
    render(<PipelineProgress progress={{ ...idle, extracting: 2, pending: 5, ready: 57 }} />);

    const summary = screen.getByRole("status", { name: "처리 현황" });
    expect(summary).toHaveTextContent("문서 64건");
    expect(summary).toHaveTextContent("텍스트 인식 중 2");
    expect(summary).toHaveTextContent("대기 중 5");
    expect(summary).toHaveTextContent("완료 57");
    expect(summary).not.toHaveTextContent("처리 중…");
    expect(summary).not.toHaveTextContent("실패");
  });

  it("처리할 문서가 남아 있으면 검색에 나타나기 전이라고 알린다", () => {
    render(<PipelineProgress progress={{ ...idle, processing: 1, ready: 3 }} />);

    expect(screen.getByText(/검색에 나타납니다/)).toBeInTheDocument();
  });

  it("모두 끝났으면 진행 안내를 내린다", () => {
    render(<PipelineProgress progress={{ ...idle, ready: 3, error: 1 }} />);

    expect(screen.getByRole("status", { name: "처리 현황" })).toHaveTextContent("실패 1");
    expect(screen.queryByText(/검색에 나타납니다/)).not.toBeInTheDocument();
  });
});
