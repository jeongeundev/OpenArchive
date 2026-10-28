// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-done");
    rerender(<StatusPanel status={{ ...status, inconsistent_documents: 2 }} error={null} />);
    expect(screen.getByTestId("consistency-count")).toHaveClass("text-wait");
    expect(screen.getByTestId("consistency-count")).not.toHaveClass("text-failed");
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
    expect(screen.getByTestId("stale-edge-count")).toHaveClass("text-done");
    rerender(<StatusPanel status={{ ...status, stale_edge_documents: 4 }} error={null} />);
    expect(screen.getByTestId("stale-edge-count")).toHaveClass("text-wait");
    expect(screen.getByTestId("stale-edge-count")).not.toHaveClass("text-failed");
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
    expect(screen.getByText("원본과 청크 버전 불일치")).toBeInTheDocument();
    expect(screen.queryByText("텍스트와 청크 버전")).not.toBeInTheDocument();
    expect(screen.getByText(/텍스트 버전과 청크 버전이 어긋난 문서 수/)).toBeInTheDocument();
    expect(screen.getByText("관계 미반영")).toBeInTheDocument();
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
    expect(screen.getByText("회수 대기").parentElement).toHaveTextContent(/^1회수 대기$/);
    expect(screen.getByText("워커가 60초 동안 응답하지 않으면 잡을 회수해 다시 처리합니다.")).toBeInTheDocument();
    expect(screen.getByText(/최근 잡 완료/)).toBeInTheDocument();
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
    const notice = screen.getByTestId("preview-unavailable-notice");
    expect(notice).toHaveTextContent(
      "미리보기 변환기가 없는 문서가 3건 있습니다. 변환기(rhwp·LibreOffice)·한글 글꼴·bubblewrap 설치를 확인한 뒤 서버에서 openarchive rebuild-previews를 실행하세요.",
    );
    expect(within(notice).getByText("openarchive rebuild-previews").tagName).toBe("CODE");
  });

  it("전부 0이면 정상 배너를 보이고 확인할 항목 링크를 숨긴다", () => {
    render(<StatusPanel status={{ ...status, jobs: { ...status.jobs, error: 0 } }} error={null} />);
    const banner = screen.getByTestId("health-banner");
    expect(banner).toHaveClass("bg-done-soft");
    expect(banner).toHaveTextContent("모든 처리가 정상입니다");
    expect(banner).toHaveTextContent("처리 실패 0건 · 정합성 불일치 0건 · 확인이 필요한 항목 0건");
    expect(banner).not.toHaveTextContent("(미리보기 변환기)");
    expect(screen.queryByRole("link", { name: "확인할 항목 보기" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-unavailable-notice")).not.toBeInTheDocument();
  });

  it("변환기 없음이 1건이면 확인 필요 항목·안내 띠·미리보기 배지로 알린다", () => {
    render(<StatusPanel status={{ ...status, jobs: { ...status.jobs, error: 0 }, preview_unavailable: 1 }} error={null} />);
    expect(screen.getByTestId("health-banner")).toHaveTextContent("확인이 필요한 항목 1건 (미리보기 변환기)");
    expect(screen.getByRole("link", { name: "확인할 항목 보기" })).toHaveAttribute("href", "#preview-unavailable-notice");
    expect(screen.getByTestId("preview-unavailable-notice")).toHaveAttribute("id", "preview-unavailable-notice");
    expect(within(screen.getByTestId("stage-preview")).getByText("확인 필요")).toHaveClass("text-attention");
    expect(screen.getByTestId("stage-preview")).toHaveClass("bg-attention-soft");
    expect(screen.getByTestId("preview-unavailable")).toHaveClass("text-attention");
  });

  it("임베딩 오류가 있으면 실패 배너와 임베딩 실패 배지를 보인다", () => {
    render(<StatusPanel status={{ ...status, jobs: { ...status.jobs, error: 1 } }} error={null} />);
    const banner = screen.getByTestId("health-banner");
    expect(banner).toHaveClass("bg-failed-soft");
    expect(banner).toHaveTextContent("확인이 필요한 처리가 있습니다");
    expect(banner).toHaveTextContent("처리 실패 1건");
    expect(within(screen.getByTestId("stage-embedding")).getByText("실패")).toHaveClass("text-failed");
    expect(screen.getByRole("link", { name: "확인할 항목 보기" })).toHaveAttribute("href", "#stage-embedding");
  });

  it("정합성 카드는 둘 다 0이면 일치, 아니면 불일치 배지를 단다", () => {
    const { rerender } = render(<StatusPanel status={status} error={null} />);
    expect(within(screen.getByTestId("consistency-card")).getByText("일치")).toHaveClass("text-done");
    rerender(<StatusPanel status={{ ...status, stale_edge_documents: 1 }} error={null} />);
    expect(within(screen.getByTestId("consistency-card")).getByText("불일치")).toHaveClass("text-failed");
  });

  it("TC 286 항목을 모두 보여준다", () => {
    render(<StatusPanel status={status} error={null} />);
    for (const label of ["원본과 청크 버전 불일치", "접속 DB 노드", "인식 대기", "인식 실패", "처리 중", "회수 대기", "오류", "BAAI/bge-m3"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(within(screen.getByTestId("stage-embedding")).getByText("대기")).toBeInTheDocument();
    expect(screen.getByText("장애 전환 시 새 Primary로 바뀝니다")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/OpenSQL|실시간|항상 최신|무중단/);
  });
});

const outage: import("@/lib/types").DbOutage = {
  id: 1, kind: "promotion", last_ok_at: "2026-10-28T06:10:00Z",
  first_failure_at: "2026-10-28T06:10:02Z", recovered_at: "2026-10-28T06:10:35Z",
  node_before: "192.168.64.201:5432", node_after: "192.168.64.202:5432",
  timeline_before: 85, timeline_after: 86, failed_checks: 10,
  requests_retried: 3, requests_unavailable: 4, server_errors: 0,
};

it("장애 기록의 시각·노드·타임라인·요청과 감사 링크를 보인다", () => {
  render(<StatusPanel status={status} error={null} outages={[outage, { ...outage, id: 2, kind: "interruption" }]} />);
  expect(screen.getByText("Primary 승격")).toHaveClass("text-attention");
  expect(screen.getByText("연결 일시 중단")).toHaveClass("text-wait");
  expect(screen.getByText(/Primary 192.168.64.201:5432 → 192.168.64.202:5432/)).toBeInTheDocument();
  expect(screen.getByText("타임라인 85 → 86")).toBeInTheDocument();
  expect(screen.getByText(/그 사이 요청 자동 재시도 성공 3건 · 503 응답 4건/)).toBeInTheDocument();
  expect(screen.getAllByText("서버 오류 0건")[0]).toHaveClass("text-done");
  expect(screen.getByText("이 시간대 감사 로그 보기")).toHaveAttribute("href", "/admin/audit?since=2026-10-28T06%3A09%3A02.000Z");
  expect(screen.getAllByText(/33초/).length).toBeGreaterThan(0);
});

it("빈 장애 기록과 관측 한계를 알린다", () => {
  render(<StatusPanel status={status} error={null} outages={[]} />);
  expect(screen.getByText("기록된 DB 장애가 없습니다")).toBeInTheDocument();
  expect(screen.getByText(/앱 연결에 영향이 없는 장애/)).toBeInTheDocument();
});

it("응답 없음 배너에서도 마지막 관측값을 보존한다", () => {
  render(<StatusPanel status={status} error={null} unresponsiveSeconds={6} lastSuccessAt={new Date(outage.last_ok_at)} />);
  expect(screen.getByTestId("health-banner")).toHaveTextContent("DB 응답 없음 — 6초째");
  expect(screen.getByTestId("health-banner")).toHaveClass("text-failed");
  expect(screen.getByTestId("consistency-count")).toHaveTextContent("0");
});


it("최초 상태 조회도 응답이 없으면 배너를 보인다", () => {
  render(<StatusPanel status={null} error={null} unresponsiveSeconds={6} />);
  expect(screen.getByTestId("health-banner")).toHaveTextContent("DB 응답 없음 — 6초째");
});
