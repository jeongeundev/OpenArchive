// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OwnerLabel } from "./OwnerLabel";

describe("OwnerLabel", () => {
  it("부서 폴더가 보이면 부서 이름이 그 폴더 링크다", () => {
    const { container } = render(
      <OwnerLabel owner="김민준" department={{ name: "IT전략실", folder_id: "f1" }} />,
    );

    expect(container).toHaveTextContent(/^김민준\(IT전략실\)$/);
    expect(screen.getByRole("link", { name: "IT전략실" })).toHaveAttribute("href", "/?folder=f1");
  });

  it("부서 폴더를 볼 수 없으면 같은 글자를 링크 없이 보인다", () => {
    const { container } = render(
      <OwnerLabel owner="김민준" department={{ name: "IT전략실", folder_id: null }} />,
    );

    expect(container).toHaveTextContent(/^김민준\(IT전략실\)$/);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("부서가 없으면 이름만 보인다", () => {
    const { container, rerender } = render(<OwnerLabel owner="김민준" />);
    expect(container).toHaveTextContent(/^김민준$/);

    rerender(<OwnerLabel owner="김민준" department={null} />);
    expect(container).toHaveTextContent(/^김민준$/);
  });
});
