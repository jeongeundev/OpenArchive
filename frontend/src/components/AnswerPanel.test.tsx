// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
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
  it("「AI 답변」 카드 머리에 열람 권한 범위를 적는다", () => {
    renderPanel(null);

    const heading = screen.getByRole("heading", { name: "AI 답변" });
    expect(heading.closest("section")).toHaveClass("rounded-xl", "bg-accent-soft");
    expect(screen.getByText("열람 권한이 있는 문서만 근거로 사용")).toHaveClass("text-xs");
  });

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

  it("답의 인용 번호는 작은 번호 칩으로 보이고, 화면 글자는 번호만이다", () => {
    renderPanel(answered("연결한다 [1]", [source({ label: 1 })]));

    const citation = within(screen.getByTestId("answer-text")).getByRole("link", { name: "[1]" });
    expect(citation).toHaveClass("rounded", "bg-card", "text-xs", "text-accent");
    const visible = Array.from(citation.childNodes)
      .filter((node) => !(node instanceof HTMLElement && node.classList.contains("sr-only")))
      .map((node) => node.textContent).join("");
    expect(visible).toBe("1");
  });

  it("답이 나오면 「답변 복사」로 답을 클립보드에 넣는다", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderPanel(answered("새 프라이머리로 연결한다 [1]", [source({ label: 1 })]));

    fireEvent.click(screen.getByRole("button", { name: "답변 복사" }));

    expect(writeText).toHaveBeenCalledWith("새 프라이머리로 연결한다 [1]");
    vi.unstubAllGlobals();
  });

  it("「다시 생성」은 같은 요청을 다시 부른다", () => {
    const onAsk = renderPanel(answered("답 [1]", [source({ label: 1 })]));

    fireEvent.click(screen.getByRole("button", { name: "다시 생성" }));

    expect(onAsk).toHaveBeenCalledTimes(1);
  });

  it("답이 없으면 「답변 복사」·「다시 생성」을 보이지 않는다", () => {
    renderPanel(null);

    expect(screen.queryByRole("button", { name: "답변 복사" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 생성" })).not.toBeInTheDocument();
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

  it("인용한 근거 목록에 화면에 보이는 제목을 붙인다 (명세서 261)", () => {
    renderPanel(answered("답 [1]", [source({ label: 1 })]));

    expect(screen.getByRole("heading", { name: "인용한 근거" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "인용한 근거" })).toBeInTheDocument();
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

  it("인용한 근거마다 형식 칩과 폴더 경로·소유자 한 줄을 보인다", () => {
    renderPanel(
      answered("답 [1] [2]", [
        source({ label: 1, content_type: "md", folder_path: ["운영", "규정"], owner: "김지은" }),
        source({ label: 2, title: "백업 절차", document_id: "document-2", content_type: "pdf", folder_path: null, owner: "이영호" }),
      ]),
    );

    const list = screen.getByRole("list", { name: "인용한 근거" });
    const [first, second] = within(list).getAllByRole("listitem");
    expect(within(first).getByTestId("source-type")).toHaveTextContent(/^MD$/);
    expect(within(first).getByText("운영 › 규정 · 김지은")).toBeInTheDocument();
    expect(within(second).getByTestId("source-type")).toHaveTextContent(/^PDF$/);
    expect(within(second).getByText("이영호")).toBeInTheDocument();
  });

  // #288 step 9 — 근거 목록을 펼친 채 lg에서 2열로 놓고, 대목은 공백을 접어 2줄로 줄인다.
  it("인용한 근거 목록을 lg에서 2열 격자로 놓는다", () => {
    renderPanel(answered("답 [1]", [source({ label: 1 })]));

    expect(screen.getByRole("list", { name: "인용한 근거" })).toHaveClass("lg:grid-cols-2");
  });

  it("인용한 근거의 대목을 펼친 채 공백을 접어 2줄로 보인다 (명세서 288)", () => {
    renderPanel(
      answered("답 [1]", [source({ label: 1, content: "  장애 시\n\nOpenProxy가   새 프라이머리로\n연결한다.  " })]),
    );

    const list = screen.getByRole("list", { name: "인용한 근거" });
    const passage = within(list).getByText("장애 시 OpenProxy가 새 프라이머리로 연결한다.");
    expect(passage.textContent).toBe("장애 시 OpenProxy가 새 프라이머리로 연결한다.");
    expect(passage.closest("details")).toBeNull();
    expect(passage).toHaveClass("line-clamp-2");
    expect(passage).not.toHaveClass("whitespace-pre-wrap");
    expect(passage.parentElement).toHaveClass("bg-sunken");
  });

  it("인용한 근거는 번호 칩을 단 카드로 보인다", () => {
    renderPanel(answered("답 [1]", [source({ label: 1 })]));

    const item = within(screen.getByRole("list", { name: "인용한 근거" })).getByRole("listitem");
    expect(item).toHaveClass("rounded-xl", "border-line", "bg-card");
    expect(within(item).getByTestId("source-label")).toHaveTextContent("[1]");
  });

  it("인용한 근거는 형식 칩 다음 메타 줄에 위치 · 소유자 순서로 적는다", () => {
    renderPanel(
      answered("답 [1]", [source({ label: 1, content_type: "docx", folder_path: ["재무"], owner: "최수진" })]),
    );

    const list = screen.getByRole("list", { name: "인용한 근거" });
    const chip = within(list).getByTestId("source-type");
    const meta = within(list).getByText("재무 · 최수진");
    expect(chip).toHaveTextContent(/^DOCX$/);
    expect(chip.compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("근거의 소유자에 부서가 있으면 `이름(부서)`로 보인다 (D16)", () => {
    renderPanel(
      answered("답 [1]", [
        source({ label: 1, content_type: "docx", folder_path: ["재무"], owner: "최수진", owner_department: { name: "IT전략실", folder_id: "f1" } }),
        source({ label: 2, title: "참고만 한 문서", document_id: "document-2", cited: false, content_type: "hwpx", folder_path: null, owner: "박민수", owner_department: { name: "운영팀", folder_id: null } }),
      ]),
    );

    const list = screen.getByRole("list", { name: "인용한 근거" });
    expect(within(list).getByText((_, node) => node?.tagName === "P" && node.textContent === "재무 · 최수진(IT전략실)")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "IT전략실" })).toHaveAttribute("href", "/documents?folder=f1");
    expect(screen.getByText((_, node) => node?.tagName === "SPAN" && node.textContent === "HWPX · 박민수(운영팀)")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "운영팀" })).not.toBeInTheDocument();
  });

  it("인용하지 않은 근거에도 형식·소유자를 보인다", () => {
    renderPanel(
      answered("답 [1]", [
        source({ label: 1 }),
        source({ label: 2, title: "참고만 한 문서", document_id: "document-2", cited: false, content_type: "hwpx", folder_path: ["운영"], owner: "박민수" }),
      ]),
    );

    expect(screen.getByText("HWPX · 박민수")).toBeInTheDocument();
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
