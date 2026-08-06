import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SystemStatus } from "@/lib/types";
import { StatusPanel } from "./StatusPanel";

const status: SystemStatus = {
  node_address: "192.168.64.4", node_port: 6432,
  jobs: { pending: 1, processing: 2, error: 3 },
  inconsistent_documents: 0, embedding_provider: "BAAI/bge-m3",
};

describe("StatusPanel", () => {
  it("정합성 0과 2를 정상 수렴 색과 처리 중 색으로 구분한다", () => {
    const { rerender } = render(<StatusPanel status={status} error={null} />);
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-[#22c55e]");
    rerender(<StatusPanel status={{ ...status, inconsistent_documents: 2 }} error={null} />);
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-[#a3a3a3]");
    expect(screen.getByTestId("consistency-count")).not.toHaveClass("text-[#ef4444]");
  });

  it("노드 주소가 없으면 유닉스 소켓과 포트를 표시한다", () => {
    render(<StatusPanel status={{ ...status, node_address: null }} error={null} />);
    expect(screen.getByText("유닉스 소켓:6432")).toBeInTheDocument();
  });

  it("조회 오류와 마지막 성공 값을 함께 표시한다", () => {
    render(<StatusPanel status={status} error="연결 실패" />);
    expect(screen.getByText("상태 조회 실패: 연결 실패")).toBeInTheDocument();
    expect(screen.getByText("192.168.64.4:6432")).toBeInTheDocument();
    expect(screen.getByText("BAAI/bge-m3")).toBeInTheDocument();
  });

  it("수집하지 않는 이벤트 카드를 표시하지 않는다", () => {
    render(<StatusPanel status={status} error={null} />);
    expect(screen.queryByText(["재", "연결 이벤트"].join(""))).not.toBeInTheDocument();
  });
});
