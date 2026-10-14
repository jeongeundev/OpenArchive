// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DocumentStatusBadge, StatusBadge } from "./StatusBadge";

function dotOf(badge: HTMLElement): Element {
  const dots = badge.querySelectorAll('[aria-hidden="true"]');
  expect(dots).toHaveLength(1);
  return dots[0];
}

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

  it.each([
    ["ready", "완료", "text-done", "bg-done-soft"],
    ["error", "실패", "text-failed", "bg-failed-soft"],
    ["pending", "대기 중", "text-wait", "bg-wait-soft"],
    ["processing", "처리 중…", "text-accent", "bg-accent-soft"],
  ] as const)("%s 상태는 의미 토큰 색의 알약과 점 하나로 표시한다", (status, label, text, bg) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByText(label);

    expect(badge).toHaveClass(text, bg, "rounded-full");
    dotOf(badge);
  });

  it("좁은 표 칸에서도 알약 안 글자가 줄바꿈되지 않는다 — 문서 목록 상태 칸에서 「완료」가 두 줄로 갈렸다", () => {
    render(<DocumentStatusBadge document={{ embedding_status: "ready", extraction_status: "done" }} />);

    expect(screen.getByText("완료")).toHaveClass("whitespace-nowrap");
  });

  it("대기는 빈 점, 완료·실패·처리 중은 채운 점이다 — 색만으로 상태를 말하지 않는다", () => {
    const { rerender } = render(<StatusBadge status="pending" />);
    const waitDot = dotOf(screen.getByText("대기 중"));
    expect(waitDot).toHaveClass("border-current");
    expect(waitDot).not.toHaveClass("bg-current");

    rerender(<StatusBadge status="ready" />);
    expect(dotOf(screen.getByText("완료"))).toHaveClass("bg-current");
  });

  it("처리 중일 때만 점이 깜빡인다", () => {
    const { rerender } = render(<StatusBadge status="processing" />);
    expect(dotOf(screen.getByText("처리 중…"))).toHaveClass("motion-safe:animate-pulse");

    for (const [status, label] of [["pending", "대기 중"], ["ready", "완료"], ["error", "실패"]] as const) {
      rerender(<StatusBadge status={status} />);
      expect(dotOf(screen.getByText(label))).not.toHaveClass("motion-safe:animate-pulse");
    }
  });

  it("텍스트 인식 배지도 같은 모양이다", () => {
    const { rerender } = render(
      <DocumentStatusBadge document={{ embedding_status: "pending", extraction_status: "pending" }} />,
    );
    const pending = screen.getByText("텍스트 인식 중");
    expect(pending).toHaveClass("text-wait", "bg-wait-soft");
    dotOf(pending);

    rerender(<DocumentStatusBadge document={{ embedding_status: "pending", extraction_status: "failed" }} />);
    expect(screen.getByText("텍스트 인식 실패")).toHaveClass("text-failed", "bg-failed-soft");
  });
});
