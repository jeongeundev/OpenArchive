import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VersionHistory } from "./VersionHistory";

const versions = [
  { version: 1, created_at: "2026-08-05T10:00:00Z" },
  { version: 3, created_at: "2026-08-05T12:00:00Z" },
  { version: 2, created_at: "2026-08-05T11:00:00Z" },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("VersionHistory", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function renderHistory(items = versions) {
    return render(<VersionHistory documentId="document-1" versions={items} currentVersion={3} disabled={false} onRestored={vi.fn()} />);
  }

  it("버전 하나에는 비교 선택 UI가 없다", () => {
    renderHistory([versions[1]]);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "비교" })).not.toBeInTheDocument();
  });

  it("두 버전만 선택하고 선택 순서와 무관하게 낮은 버전을 base로 비교한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ base: 1, target: 3, identical: true, hunks: [] }));
    vi.stubGlobal("fetch", fetchMock);
    renderHistory();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    const compare = screen.getByRole("button", { name: "비교" });
    expect(compare).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "v3 비교 선택" }));
    expect(compare).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "v1 비교 선택" }));
    expect(compare).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "v2 비교 선택" })).toBeDisabled();
    fireEvent.click(compare);
    await screen.findByText("두 버전의 내용이 같습니다.");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/document-1/versions/1/diff/3");
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    fireEvent.click(screen.getByRole("checkbox", { name: "v3 비교 선택" }));
    expect(screen.getByRole("checkbox", { name: "v2 비교 선택" })).toBeEnabled();
  });

  it("현재와 비교는 과거 버전에만 있고 서버의 줄과 덩어리를 구분해 표시하고 닫는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ base: 1, target: 3, identical: false, hunks: [
      { lines: [{ op: "equal", text: "맥락" }, { op: "removed", text: "이전 줄" }, { op: "added", text: "새 줄" }] },
      { lines: [{ op: "added", text: "다른 대목" }] },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    renderHistory();
    expect(screen.getAllByRole("button", { name: "현재와 비교" })).toHaveLength(2);
    expect(screen.getByText("v3").closest("li")?.textContent).not.toContain("현재와 비교");
    fireEvent.click(screen.getAllByRole("button", { name: "현재와 비교" })[1]);
    await screen.findByRole("heading", { name: "v1 ↔ v3 비교" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/document-1/versions/1/diff/3");
    const added = screen.getByText("+ 새 줄");
    expect(added).toHaveAttribute("data-op", "added");
    expect(added).toHaveClass("text-green-400");
    const removed = screen.getByText("− 이전 줄");
    expect(removed).toHaveAttribute("data-op", "removed");
    expect(removed).toHaveClass("text-red-400");
    expect(screen.getByText("맥락")).toHaveAttribute("data-op", "equal");
    expect(screen.getByText("맥락")).toHaveClass("text-neutral-400");
    expect(screen.getByText("…")).toBeInTheDocument();
    expect(added.closest(".overflow-auto")).toHaveClass("max-h-96", "overflow-auto");
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(screen.queryByRole("heading", { name: "v1 ↔ v3 비교" })).not.toBeInTheDocument();
  });

  it("내용은 다르지만 줄 차이가 없으면 줄 끝 개행 차이를 알린다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ base: 1, target: 3, identical: false, hunks: [] })));
    renderHistory();
    fireEvent.click(screen.getAllByRole("button", { name: "현재와 비교" })[1]);
    expect(await screen.findByText("줄 끝 개행만 다릅니다.")).toBeInTheDocument();
  });

  it("비교 오류는 서버 detail을 표시한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "문서를 찾을 수 없습니다." }, 404)));
    renderHistory();
    fireEvent.click(screen.getAllByRole("button", { name: "현재와 비교" })[1]);
    expect(await screen.findByText("문서를 찾을 수 없습니다.")).toBeInTheDocument();
  });

  it("버전을 내림차순으로 표시하고 현재 버전을 표시한다", () => {
    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={vi.fn()}
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
    render(
      <VersionHistory
        documentId="document-1"
        versions={[]}
        currentVersion={1}
        disabled={false}
        onRestored={vi.fn()}
      />,
    );

    expect(screen.getByText("편집 이력이 없습니다.")).toBeInTheDocument();
  });

  it("현재 버전에는 되돌리기를 노출하지 않는다", () => {
    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={vi.fn()}
      />,
    );

    // v2·v1 두 개만 되돌릴 수 있다. v3은 이미 그 내용이다.
    expect(screen.getAllByRole("button", { name: "되돌리기" })).toHaveLength(2);
  });

  it("본문 보기를 누르면 그 버전의 텍스트를 불러와 보여준다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ version: 1, content: "처음 내용", created_at: versions[0].created_at }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={vi.fn()}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "본문 보기" })[2]);

    await waitFor(() => {
      expect(screen.getByText("처음 내용")).toBeInTheDocument();
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/document-1/versions/1");
  });

  // 답변 인용 클릭 → 그 버전의 그 자리 (ADR-043 결정 3, #96 b). 위치는 서버가 UTF-16 단위로 준다.
  it("가리킨 버전을 펼치고 대목을 강조해 그 자리로 스크롤한다", async () => {
    const content = "🚀 머리말\n\n둘째 대목이다.\n\n셋째";
    const passage = "둘째 대목이다.";
    const start = content.indexOf(passage);
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        version: 2,
        content,
        created_at: versions[2].created_at,
        passage_start: start,
        passage_end: start + passage.length,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        focus={{ version: 2, chunk: 1 }}
        onRestored={vi.fn()}
      />,
    );

    const mark = await screen.findByText(passage, { selector: "mark" });
    expect(mark.closest("pre")).toHaveTextContent("🚀 머리말 둘째 대목이다. 셋째");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/document-1/versions/2?chunk=1");
    expect(scrollIntoView).toHaveBeenCalled();
  });

  // 같은 문서 안에서 다른 인용으로 클라이언트 이동하면 컴포넌트가 다시 마운트되지 않는다.
  it("가리킨 자리가 바뀌면 그 버전을 펼치고 이전 강조를 지운다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          version: 2,
          content: "둘째 판 대목",
          created_at: versions[2].created_at,
          passage_start: 0,
          passage_end: "둘째 판 대목".length,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ version: 1, content: "첫째 판 본문", created_at: versions[0].created_at }),
      );
    vi.stubGlobal("fetch", fetchMock);
    Element.prototype.scrollIntoView = vi.fn();
    const props = {
      documentId: "document-1",
      versions,
      currentVersion: 3,
      disabled: false,
      onRestored: vi.fn(),
    };

    const { rerender } = render(<VersionHistory {...props} focus={{ version: 2, chunk: 0 }} />);
    await screen.findByText("둘째 판 대목", { selector: "mark" });

    rerender(<VersionHistory {...props} focus={{ version: 1, chunk: 9 }} />);

    expect(await screen.findByText("첫째 판 본문")).toBeInTheDocument();
    expect(screen.queryByText("둘째 판 대목")).not.toBeInTheDocument();
    expect(document.querySelector("mark")).toBeNull();
  });

  it("같은 버전의 위치 없는 대목으로 옮기면 이전 강조가 남지 않는다", async () => {
    const content = "첫 대목\n\n둘째 대목";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          version: 2,
          content,
          created_at: versions[2].created_at,
          passage_start: 0,
          passage_end: "첫 대목".length,
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ version: 2, content, created_at: versions[2].created_at }));
    vi.stubGlobal("fetch", fetchMock);
    Element.prototype.scrollIntoView = vi.fn();
    const props = {
      documentId: "document-1",
      versions,
      currentVersion: 3,
      disabled: false,
      onRestored: vi.fn(),
    };

    const { rerender } = render(<VersionHistory {...props} focus={{ version: 2, chunk: 0 }} />);
    await screen.findByText("첫 대목", { selector: "mark" });

    rerender(<VersionHistory {...props} focus={{ version: 2, chunk: 9 }} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(document.querySelector("mark")).toBeNull();
  });

  it("가리킨 버전을 불러오지 못하면 펼침을 닫고 오류를 알린다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "버전을 찾을 수 없습니다." }, 404)),
    );

    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        focus={{ version: 2, chunk: 0 }}
        onRestored={vi.fn()}
      />,
    );

    expect(await screen.findByText("버전을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
  });

  it("되돌리기는 새 버전이 생긴다고 알린 뒤에 실행한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ version: 4 }));
    vi.stubGlobal("fetch", fetchMock);
    const onRestored = vi.fn();

    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={onRestored}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "되돌리기" })[1]);

    // 되감기로 오해하지 않도록 확인 단계에서 새 버전이 생긴다는 것을 밝힌다 (ADR-037).
    expect(
      screen.getByText("v1의 내용으로 새 텍스트 버전을 만듭니다. 이력은 지워지지 않습니다."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "새 버전 만들기" }));

    await waitFor(() => {
      expect(onRestored).toHaveBeenCalled();
    });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/documents/document-1/versions/1/restore",
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      current_version: 3,
    });
  });

  it("409를 받으면 덮어쓰지 않고 새로고침을 안내한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            detail: "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
            current_version: 5,
          },
          409,
        ),
      ),
    );
    const onRestored = vi.fn();

    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={onRestored}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "되돌리기" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "새 버전 만들기" }));

    await waitFor(() => {
      expect(
        screen.getByText(/다른 곳에서 문서가 수정되었습니다\..*현재 서버 버전: v5/),
      ).toBeInTheDocument();
    });
    expect(onRestored).not.toHaveBeenCalled();
  });

  it("쓰기 권한이 없으면 되돌리기를 노출하지 않는다", () => {
    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled
        onRestored={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "되돌리기" })).not.toBeInTheDocument();
    // 열람은 막지 않는다 — 볼 수 있는 문서의 과거 본문은 볼 수 있다.
    expect(screen.getAllByRole("button", { name: "본문 보기" })).toHaveLength(3);
  });

  it("막힌 이유가 있으면 되돌리기를 비활성으로 두고 이유를 보인다", () => {
    render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        restoreBlockedReason="원본에서 텍스트를 인식하는 중입니다."
        onRestored={vi.fn()}
      />,
    );

    for (const button of screen.getAllByRole("button", { name: "되돌리기" })) {
      expect(button).toBeDisabled();
    }
    expect(screen.getByText("원본에서 텍스트를 인식하는 중입니다.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "본문 보기" })).toHaveLength(3);
  });
});

describe("VersionHistory 취소", () => {
  it("본문을 받는 중에 화면을 떠나면 요청을 취소한다", () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(
      <VersionHistory
        documentId="document-1"
        versions={versions}
        currentVersion={3}
        disabled={false}
        onRestored={vi.fn()}
      />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "본문 보기" })[2]);
    unmount();

    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    vi.unstubAllGlobals();
  });
});
