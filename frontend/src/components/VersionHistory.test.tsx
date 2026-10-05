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
