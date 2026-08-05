import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["pending", "대기 중"],
    ["processing", "처리 중…"],
    ["ready", "완료"],
    ["error", "실패"],
  ] as const)("%s 상태를 한국어로 표시한다", (status, label) => {
    render(<StatusBadge status={status} />);

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("완료와 실패 상태를 서로 다른 색으로 표시한다", () => {
    const { rerender } = render(<StatusBadge status="ready" />);
    const readyClass = screen.getByText("완료").className;

    rerender(<StatusBadge status="error" />);

    expect(screen.getByText("실패").className).not.toBe(readyClass);
  });

  it("대기와 처리 상태를 같은 색과 다른 라벨로 표시한다", () => {
    const { rerender } = render(<StatusBadge status="pending" />);
    const pending = screen.getByText("대기 중");
    const pendingClass = pending.className;

    rerender(<StatusBadge status="processing" />);
    const processing = screen.getByText("처리 중…");

    expect(processing).toHaveClass(...pendingClass.split(" "));
    expect(processing).not.toHaveTextContent("대기 중");
  });
});
