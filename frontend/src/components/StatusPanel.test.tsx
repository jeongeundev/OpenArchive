import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SystemStatus } from "@/lib/types";
import { StatusPanel } from "./StatusPanel";

const status: SystemStatus = {
  node_address: "192.168.64.4", node_port: 6432,
  jobs: { pending: 1, processing: 2, recovery_pending: 1, error: 3 },
  job_lease_seconds: 60,
  last_job_finished_at: "2026-08-11T01:23:45Z",
  inconsistent_documents: 0, stale_edge_documents: 0, extraction_pending: 0, extraction_failed: 0, preview_pending: 0, preview_failed: 0, preview_unavailable: 0, embedding_provider: "BAAI/bge-m3",
};

describe("StatusPanel", () => {
  it("정합성 0과 2를 정상 수렴 색과 처리 중 색으로 구분한다", () => {
    const { rerender } = render(<StatusPanel status={status} error={null} />);
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-[#22c55e]");
    rerender(<StatusPanel status={{ ...status, inconsistent_documents: 2 }} error={null} />);
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-[#a3a3a3]");
    expect(screen.getByTestId("consistency-count")).not.toHaveClass("text-[#ef4444]");
  });

  it("관계가 다 반영되면 반영됨을 알리고, 남아 있으면 그 건수를 보여준다", () => {
    const { rerender } = render(<StatusPanel status={status} error={null} />);
    expect(screen.getByTestId("stale-edge-count")).toHaveTextContent("0");
    expect(screen.getByText(/관계까지 반영됨/)).toBeInTheDocument();
    rerender(<StatusPanel status={{ ...status, stale_edge_documents: 4 }} error={null} />);
    expect(screen.getByTestId("stale-edge-count")).toHaveTextContent("4");
    expect(screen.queryByText(/관계까지 반영됨/)).not.toBeInTheDocument();
  });

  it("관계 미반영 건수도 0과 그 외를 색으로 가르되 오류 색을 쓰지 않는다", () => {
    const { rerender } = render(<StatusPanel status={status} error={null} />);
    expect(screen.getByTestId("stale-edge-count")).toHaveClass("text-[#22c55e]");
    rerender(<StatusPanel status={{ ...status, stale_edge_documents: 4 }} error={null} />);
    expect(screen.getByTestId("stale-edge-count")).toHaveClass("text-[#a3a3a3]");
    expect(screen.getByTestId("stale-edge-count")).not.toHaveClass("text-[#ef4444]");
  });

  it("관계 미반영이 남아 있을 때 저절로 0이 된다고 단정하지 않고 복구 경로를 알린다", () => {
    // 관계 잡이 재시도를 소진해 error로 격리되면 rebuild-edges 전에는 내려오지 않는다.
    render(<StatusPanel status={{ ...status, stale_edge_documents: 4 }} error={null} />);
    const card = screen.getByTestId("stale-edge-count").parentElement;
    expect(card?.textContent).not.toMatch(/0으로 돌아옵니다/);
    expect(card?.textContent).toMatch(/openarchive rebuild-edges/);
  });

  it("두 수가 무엇을 세는지 레이블로 구분한다", () => {
    render(<StatusPanel status={{ ...status, inconsistent_documents: 2, stale_edge_documents: 4 }} error={null} />);
    expect(screen.getByText("원본과 청크 버전")).toBeInTheDocument();
    expect(screen.getByText("관계")).toBeInTheDocument();
    expect(screen.getByTestId("consistency-count")).toHaveTextContent("2");
    expect(screen.getByTestId("stale-edge-count")).toHaveTextContent("4");
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
    expect(screen.queryByText("재연결 이벤트")).not.toBeInTheDocument();
  });

  it("lease가 만료된 처리 중 잡을 회수 대기로 표시하고 관측 가능한 완료 시각만 보여준다", () => {
    render(<StatusPanel status={status} error={null} />);
    expect(screen.getByText("회수 대기").parentElement).toHaveTextContent("회수 대기1");
    expect(screen.getByText("워커가 60초 동안 처리 중임을 알리지 않은 잡은 회수됩니다.")).toBeInTheDocument();
    expect(screen.getByText(/최근 잡 완료 시각/)).toBeInTheDocument();
    expect(screen.queryByText(/워커 정상/)).not.toBeInTheDocument();
  });

  it("텍스트 인식 대기·실패 문서 수를 보여준다", () => {
    render(<StatusPanel status={{ ...status, extraction_pending: 2, extraction_failed: 1 }} error={null} />);

    const card = screen.getByText("텍스트 인식").closest("div");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByTestId("extraction-pending")).toHaveTextContent("2");
    expect(within(card as HTMLElement).getByTestId("extraction-failed")).toHaveTextContent("1");
  });

  it("미리보기 변환 대기·실패·변환기 없음 판 수를 보여준다", () => {
    render(<StatusPanel status={{ ...status, preview_pending: 4, preview_failed: 2, preview_unavailable: 0 }} error={null} />);

    const card = screen.getByText("미리보기 변환").closest("div");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByTestId("preview-pending")).toHaveTextContent("4");
    expect(within(card as HTMLElement).getByTestId("preview-failed")).toHaveTextContent("2");
    expect(within(card as HTMLElement).getByTestId("preview-unavailable")).toHaveTextContent("0");
    expect(screen.queryByText(/rebuild-previews/)).not.toBeInTheDocument();
  });

  it("변환기 없음이 있으면 설치 확인과 rebuild-previews를 안내한다", () => {
    render(<StatusPanel status={{ ...status, preview_unavailable: 3 }} error={null} />);

    expect(screen.getByTestId("preview-unavailable")).toHaveTextContent("3");
    expect(
      screen.getByText("변환기(rhwp·LibreOffice)·한글 글꼴·bubblewrap 설치를 확인하세요. 설치 뒤 openarchive rebuild-previews로 다시 겁니다."),
    ).toBeInTheDocument();
  });
});
