import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VersionHistory } from "./VersionHistory";

describe("VersionHistory", () => {
  it("버전을 내림차순으로 표시하고 현재 버전을 표시한다", () => {
    render(
      <VersionHistory
        versions={[
          { version: 1, created_at: "2026-08-05T10:00:00Z" },
          { version: 3, created_at: "2026-08-05T12:00:00Z" },
          { version: 2, created_at: "2026-08-05T11:00:00Z" },
        ]}
        currentVersion={3}
      />,
    );

    expect(screen.getByRole("heading", { name: "텍스트 버전 이력" })).toBeInTheDocument();
    expect(screen.getAllByText(/^v\d$/).map((item) => item.textContent)).toEqual([
      "v3",
      "v2",
      "v1",
    ]);
    expect(screen.getByText("현재")).toBeInTheDocument();
  });

  it("이력이 비어 있으면 편집 이력 안내를 표시한다", () => {
    render(<VersionHistory versions={[]} currentVersion={1} />);

    expect(screen.getByText("편집 이력이 없습니다.")).toBeInTheDocument();
  });
});
