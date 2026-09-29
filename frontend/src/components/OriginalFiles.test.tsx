import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EXTRACTING_NOTICE, type DocumentDetail } from "@/lib/types";
import { OriginalFiles } from "./OriginalFiles";

const document: DocumentDetail = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide-v2.pdf",
  content_type: "pdf",
  content: "추출된 텍스트",
  version: 3,
  owner_id: "alice",
  visibility: "public",
  tags: [],
  embedding_status: "ready",
  extraction_status: "done",
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-23T11:00:00Z",
  versions: [],
  files: [
    {
      file_version: 1,
      filename: "guide.pdf",
      size: 2048,
      sha256: "a".repeat(64),
      text_version: 1,
      uploaded_by: "alice",
      uploaded_at: "2026-09-20T10:00:00Z",
    },
    {
      file_version: 2,
      filename: "guide-v2.pdf",
      size: 3_500_000,
      sha256: "b".repeat(64),
      text_version: 3,
      uploaded_by: "alice",
      uploaded_at: "2026-09-23T11:00:00Z",
    },
  ],
  chunk_count: 1,
  chunk_version: 3,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pickFile(file: File): void {
  fireEvent.change(screen.getByLabelText("새 원본 파일"), { target: { files: [file] } });
}

describe("OriginalFiles", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("판마다 파일명·크기·텍스트 버전을 보여주고 최신 판에 현재를 표시한다", () => {
    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    // 최신 판이 먼저 온다.
    expect(items[0]).toHaveTextContent("2판");
    expect(items[0]).toHaveTextContent("guide-v2.pdf");
    expect(items[0]).toHaveTextContent("3.5MB");
    expect(items[0]).toHaveTextContent("텍스트 v3");
    expect(items[0]).toHaveTextContent("현재");
    expect(items[1]).toHaveTextContent("1판");
    expect(items[1]).toHaveTextContent("2.0KB");
    expect(items[1]).not.toHaveTextContent("현재");
  });

  it("최신 판은 /file, 이전 판은 /files/{n}로 내려받는다", () => {
    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);

    const links = screen.getAllByRole("link", { name: "내려받기" });
    expect(links[0]).toHaveAttribute("href", "/api/documents/document-1/file");
    expect(links[1]).toHaveAttribute("href", "/api/documents/document-1/files/1");
    for (const link of links) expect(link).toHaveAttribute("download");
  });

  it("원본이 없는 문서에는 올리기만 보이고 내려받기·다시 추출이 없다", () => {
    render(
      <OriginalFiles
        anonymous={false}
        disabled={false}
        document={{ ...document, filename: null, content_type: "md", files: [] }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("원본 파일이 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "원본 파일 올리기" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "내려받기" })).toBeNull();
    expect(screen.queryByRole("button", { name: /다시 추출/ })).toBeNull();
    // 원본이 없는 문서에는 "추출"이라는 말을 쓰지 않는다.
    expect(screen.queryByText(/추출/)).toBeNull();
  });

  it("익명에게는 절 자체를 보여주지 않는다", () => {
    const { container } = render(
      <OriginalFiles anonymous disabled={false} document={document} onChanged={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("확인하면 읽어온 버전과 함께 새 파일로 교체하고 갱신한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...document, version: 4 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();
    const file = new File(["%PDF"], "guide-v3.pdf");

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={onChanged} />);
    pickFile(file);

    expect(window.confirm).toHaveBeenCalledWith(
      "새 원본 파일로 교체합니다. 이전 원본은 판 목록에 남고, 새 파일에서 추출한 텍스트가 새 버전이 됩니다.",
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/documents/document-1/file");
    expect(init?.method).toBe("PUT");
    const body = init?.body as FormData;
    expect(body.get("file")).toBe(file);
    expect(body.get("current_version")).toBe("3");
  });

  it("교체를 취소하면 전송하지 않는다", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);
    pickFile(new File(["x"], "x.pdf"));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("교체가 409면 서버 문구와 현재 버전을 보여주고 갱신하지 않는다", async () => {
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
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={onChanged} />);
    pickFile(new File(["x"], "x.pdf"));

    expect(
      await screen.findByText(
        "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요. 현재 서버 버전: v5",
      ),
    ).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("50MB를 넘는 파일은 전송하지 않고 상한 문구를 보여준다", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const big = new File(["big"], "big.pdf");
    Object.defineProperty(big, "size", { value: 50_000_001 });

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);
    pickFile(big);

    expect(screen.getByText("업로드 파일은 50MB를 넘을 수 없습니다.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("다시 추출은 확인 후 읽어온 버전을 보내고, 새 버전이 생기면 갱신한다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ...document, version: 4, changed: true }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "원본에서 다시 추출" }));

    expect(window.confirm).toHaveBeenCalledWith(
      "최신 원본 파일에서 텍스트를 다시 추출합니다. 추출 텍스트를 직접 고친 내용은 새 버전으로 덮이며, 이전 내용은 버전 이력에서 되돌릴 수 있습니다.",
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/documents/document-1/reextract");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ current_version: 3 });
  });

  it("다시 추출 결과가 같으면 새 버전을 만들지 않았다고 안내한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ...document, changed: false })),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "원본에서 다시 추출" }));

    expect(
      await screen.findByText("추출 결과가 현재 텍스트와 같아 새 버전을 만들지 않았습니다."),
    ).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("소유자가 아니면 서버의 거부 문구를 그대로 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "문서를 수정할 권한이 없습니다." }, 403)),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "원본에서 다시 추출" }));

    expect(await screen.findByText("문서를 수정할 권한이 없습니다.")).toBeInTheDocument();
  });

  it("편집 중에는 교체와 다시 추출을 막는다", () => {
    render(<OriginalFiles anonymous={false} disabled document={document} onChanged={vi.fn()} />);

    expect(screen.getByRole("button", { name: "새 파일로 교체" })).toBeDisabled();
    expect(screen.getByLabelText("새 원본 파일")).toBeDisabled();
    expect(screen.getByRole("button", { name: "원본에서 다시 추출" })).toBeDisabled();
  });

  it("이미지 파일로도 교체할 수 있다", () => {
    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={vi.fn()} />);

    const accept = screen.getByLabelText("새 원본 파일").getAttribute("accept") ?? "";
    expect(accept.split(",")).toEqual(expect.arrayContaining([".png", ".jpg", ".jpeg"]));
  });

  it("텍스트 인식 중에는 교체와 다시 추출을 막고 이유를 보인다", () => {
    render(
      <OriginalFiles
        anonymous={false}
        disabled={false}
        document={{ ...document, extraction_status: "pending" }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "새 파일로 교체" })).toBeDisabled();
    expect(screen.getByLabelText("새 원본 파일")).toBeDisabled();
    expect(screen.getByRole("button", { name: "원본에서 다시 추출" })).toBeDisabled();
    expect(screen.getByText(EXTRACTING_NOTICE)).toBeInTheDocument();
  });

  it("아직 인식되지 않은 판은 텍스트 버전 대신 인식 전으로 표시한다", () => {
    render(
      <OriginalFiles
        anonymous={false}
        disabled={false}
        document={{
          ...document,
          extraction_status: "pending",
          files: [{ ...document.files[0], text_version: null }],
        }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("텍스트 인식 전")).toBeInTheDocument();
    expect(screen.queryByText(/텍스트 vnull/)).not.toBeInTheDocument();
  });

  it("다시 추출이 텍스트 인식으로 넘어가면 같다는 안내 없이 갱신한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ ...document, extraction_status: "pending", changed: false }),
        ),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();

    render(<OriginalFiles anonymous={false} disabled={false} document={document} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "원본에서 다시 추출" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(
      screen.queryByText("추출 결과가 현재 텍스트와 같아 새 버전을 만들지 않았습니다."),
    ).not.toBeInTheDocument();
  });
});
