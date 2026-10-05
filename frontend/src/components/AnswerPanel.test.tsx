import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AnswerSource, AskResponse } from "@/lib/types";
import { AnswerPanel } from "./AnswerPanel";

function source(overrides: Partial<AnswerSource>): AnswerSource {
  return {
    label: 1,
    document_id: "document-1",
    title: "운영 규정",
    chunk_index: 0,
    based_on_version: 1,
    current_version: 1,
    revised: false,
    content: "장애 시 OpenProxy가 새 프라이머리로 연결한다.",
    cited: true,
    ...overrides,
  };
}

function answered(answer: string, sources: AnswerSource[]): AskResponse {
  return { status: "answered", answer, detail: null, sources, items: [] };
}

function renderPanel(response: AskResponse | null, onAsk = vi.fn()) {
  render(<AnswerPanel response={response} loading={false} error={null} onAsk={onAsk} />);
  return onAsk;
}

describe("AnswerPanel", () => {
  it("답변 전에는 버튼으로 요청하고, 보장이 아님을 밝힌다", () => {
    const onAsk = renderPanel(null);

    expect(screen.getByText(/보장은 아닙니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이 검색어로 답변 받기" }));
    expect(onAsk).toHaveBeenCalledTimes(1);
  });

  it("생성 중에는 버튼 대신 진행을 알린다", () => {
    render(<AnswerPanel response={null} loading error={null} onAsk={vi.fn()} />);

    expect(screen.getByText(/답변을 만드는 중/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "이 검색어로 답변 받기" })).not.toBeInTheDocument();
  });

  // ADR-043 결정 3 — 인용 클릭은 그 버전의 그 자리로 간다.
  it("답의 [번호]를 근거의 그 버전·그 대목 링크로 바꾼다", () => {
    renderPanel(
      answered("새 프라이머리로 연결한다 [1]. 확인되지 않은 번호 [9]는 그대로 둔다.", [
        source({ label: 1, document_id: "doc/1", based_on_version: 3, chunk_index: 2 }),
      ]),
    );

    const answer = screen.getByTestId("answer-text");
    const citation = within(answer).getByRole("link", { name: "[1]" });
    expect(citation).toHaveAttribute("href", "/documents/doc%2F1?version=3&chunk=2");
    expect(within(answer).queryByRole("link", { name: "[9]" })).not.toBeInTheDocument();
    expect(answer).toHaveTextContent("[9]");
  });

  it("개정된 근거에는 근거 버전과 현재 버전을 함께 보인다", () => {
    renderPanel(
      answered("이전 규정 기준이다 [1] [2]", [
        source({ label: 1, title: "보존 규정", based_on_version: 3, current_version: 4, revised: true }),
        source({ label: 2, title: "운영 규정", document_id: "document-2" }),
      ]),
    );

    const list = screen.getByRole("list", { name: "인용한 근거" });
    expect(within(list).getByText("v3 기준 · 현재 v4")).toBeInTheDocument();
    expect(within(list).getByText("v1 기준")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "보존 규정" })).toHaveAttribute(
      "href",
      "/documents/document-1?version=3&chunk=0",
    );
  });

  it("모델에 줬지만 인용하지 않은 근거는 따로 접어 둔다", () => {
    renderPanel(
      answered("답 [1]", [
        source({ label: 1 }),
        source({ label: 2, title: "참고만 한 문서", document_id: "document-2", cited: false }),
      ]),
    );

    const cited = screen.getByRole("list", { name: "인용한 근거" });
    expect(within(cited).queryByText("참고만 한 문서")).not.toBeInTheDocument();
    expect(screen.getByText("인용하지 않은 근거 1건")).toBeInTheDocument();
  });

  it.each([
    [{ status: "disabled", detail: null }, /답변 생성이 설정되지 않았습니다/],
    [{ status: "no_evidence", detail: null }, /근거로 쓸 문서를 찾지 못했습니다/],
    [{ status: "failed", detail: "답변 생성에 실패했습니다." }, /답변 생성에 실패했습니다\. 검색 결과는/],
  ] as const)("답하지 못한 상태 %o를 알린다", (state, message) => {
    renderPanel({ answer: null, sources: [], items: [], ...state });

    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("요청 자체가 실패하면 오류를 보이고 다시 요청할 수 있다", () => {
    const onAsk = vi.fn();
    render(<AnswerPanel response={null} loading={false} error="로그인이 필요합니다." onAsk={onAsk} />);

    expect(screen.getByRole("status")).toHaveTextContent("로그인이 필요합니다.");
    fireEvent.click(screen.getByRole("button", { name: "이 검색어로 답변 받기" }));
    expect(onAsk).toHaveBeenCalled();
  });
});
