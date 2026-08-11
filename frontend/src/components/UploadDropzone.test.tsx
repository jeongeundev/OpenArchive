import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UploadDropzone } from "./UploadDropzone";

describe("UploadDropzone", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("선택한 파일을 multipart 요청으로 업로드한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("{}", {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["text"], "guide.txt", { type: "text/plain" });

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    const body = fetchMock.mock.calls[0][1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBe(file);
  });

  it("쉼표로 구분한 태그의 공백과 빈 항목을 제거해 전송한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("{}", {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["text"], "guide.txt")] },
    });
    fireEvent.change(screen.getByLabelText("태그 (쉼표로 구분)"), {
      target: { value: "규정, 인사,  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.getAll("tags")).toEqual(["규정", "인사"]);
  });

  it("백엔드의 400 detail을 그대로 표시한다", async () => {
    const detail = "문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다.";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["pdf"], "scan.pdf")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    expect(await screen.findByText(detail)).toHaveClass("text-[#ef4444]");
  });

  it("성공하면 폼을 초기화하고 목록 새로고침을 요청한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{}", {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const onUploaded = vi.fn();

    render(<UploadDropzone onUploaded={onUploaded} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["text"], "guide.txt")] },
    });
    fireEvent.change(screen.getByLabelText("제목 (선택)"), {
      target: { value: "운영 가이드" },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("제목 (선택)")).toHaveValue("");
    expect(
      screen.getByText("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다."),
    ).toBeInTheDocument();
  });

});
