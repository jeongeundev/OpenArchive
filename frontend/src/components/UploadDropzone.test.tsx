import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import JSZip from "jszip";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UploadDropzone } from "./UploadDropzone";

// Response 본문은 1회용이라 다건 업로드 모킹은 호출마다 새 Response를 만들어야 한다.
function jsonResponse(body: string = "{}", status = 201): Response {
  return new Response(body, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function archiveFile(zip: JSZip, name = "documents.zip"): Promise<File> {
  const blob = await zip.generateAsync({ type: "blob" });
  return new File([blob], name, { type: "application/zip" });
}

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

  it("여러 파일을 선택하면 파일별로 POST하고 태그와 공개범위를 모든 요청에 넣는다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const first = new File(["a"], "a.txt");
    const second = new File(["b"], "b.md");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [first, second] },
    });
    fireEvent.change(screen.getByLabelText("태그 (쉼표로 구분)"), {
      target: { value: "규정" },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const firstBody = fetchMock.mock.calls[0][1]?.body as FormData;
    const secondBody = fetchMock.mock.calls[1][1]?.body as FormData;
    expect(firstBody.get("file")).toBe(first);
    expect(secondBody.get("file")).toBe(second);
    expect(firstBody.getAll("tags")).toEqual(["규정"]);
    expect(secondBody.getAll("tags")).toEqual(["규정"]);
    expect(firstBody.get("visibility")).toBe("public");
    expect(firstBody.get("title")).toBeNull();
    expect(secondBody.get("title")).toBeNull();
    await screen.findByText("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다.");
  });

  it("앞 파일 업로드가 끝나기 전에는 다음 파일을 전송하지 않는다", async () => {
    let releaseFirst: (response: Response) => void = () => {};
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            releaseFirst = resolve;
          }),
      )
      .mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    releaseFirst(jsonResponse());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다.");
  });

  it("한 파일이 실패해도 나머지를 계속 업로드하고 실패 행에 이유를 표시한다", async () => {
    const detail = "문서에서 텍스트를 추출하지 못했습니다.";
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(jsonResponse(JSON.stringify({ detail }), 400)),
      )
      .mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const onUploaded = vi.fn();

    render(<UploadDropzone onUploaded={onUploaded} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(detail)).toHaveClass("text-[#ef4444]");
    expect(screen.getByText("a.txt — 실패")).toBeInTheDocument();
    expect(screen.getByText("b.txt — 완료")).toBeInTheDocument();
    expect(
      screen.getByText("일부 파일을 업로드하지 못했습니다."),
    ).toBeInTheDocument();
    expect(onUploaded).toHaveBeenCalledOnce();
  });

  it("모든 파일이 실패하면 목록 새로고침을 요청하지 않는다", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        jsonResponse(JSON.stringify({ detail: "지원하지 않는 파일 형식입니다." }), 400),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onUploaded = vi.fn();

    render(<UploadDropzone onUploaded={onUploaded} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await screen.findByText("업로드에 실패했습니다.");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it("여러 파일을 선택하면 제목 입력을 숨긴다", () => {
    render(<UploadDropzone onUploaded={vi.fn()} />);
    const input = screen.getByLabelText("업로드할 파일");

    fireEvent.change(input, {
      target: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });
    expect(screen.queryByLabelText("제목 (선택)")).toBeNull();

    fireEvent.change(input, { target: { files: [new File(["a"], "a.txt")] } });
    expect(screen.getByLabelText("제목 (선택)")).toBeInTheDocument();
  });

  it("10MB를 넘는 파일은 전송하지 않고 실패로 표시한다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const big = new File(["big"], "big.txt");
    Object.defineProperty(big, "size", { value: 10_000_001 });
    const small = new File(["small"], "small.txt");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [big, small] },
    });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect((fetchMock.mock.calls[0][1]?.body as FormData).get("file")).toBe(small);
    expect(screen.getByText("big.txt — 실패")).toBeInTheDocument();
    expect(
      screen.getByText("업로드 파일은 10MB를 넘을 수 없습니다."),
    ).toBeInTheDocument();
    await screen.findByText("일부 파일을 업로드하지 못했습니다.");
  });

  it("드롭으로 여러 파일을 넣을 수 있다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.drop(screen.getByText("파일을 끌어놓거나 클릭해 선택하세요."), {
      dataTransfer: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });
    expect(screen.getByText("2개 파일 선택됨")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText("업로드했습니다. 임베딩이 끝나면 상태가 완료로 바뀝니다.");
  });

  it("ZIP의 지원 문서를 basename 파일명으로 업로드한다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const zip = new JSZip();
    zip.file("docs/guide.txt", "guide");
    zip.file("notes/readme.md", "readme");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [await archiveFile(zip)] },
    });
    await screen.findByText("guide.txt — 대기");
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const names = fetchMock.mock.calls.map(
      (call) => ((call[1]?.body as FormData).get("file") as File).name,
    );
    expect(names).toEqual(["guide.txt", "readme.md"]);
  });

  it("ZIP의 미지원 항목은 건너뜀으로 표시하고 요청하지 않는다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const zip = new JSZip();
    zip.file("guide.txt", "guide");
    zip.file("images/logo.png", "image");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [await archiveFile(zip)] },
    });
    expect(await screen.findByText("images/logo.png — 건너뜀")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(((fetchMock.mock.calls[0][1]?.body as FormData).get("file") as File).name).toBe(
      "guide.txt",
    );
  });

  it("ZIP과 일반 파일을 함께 선택하면 모두 업로드한다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const zip = new JSZip();
    zip.file("inside.md", "inside");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [await archiveFile(zip), new File(["plain"], "plain.txt")] },
    });
    await screen.findByText("inside.md — 대기");
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const names = fetchMock.mock.calls.map(
      (call) => ((call[1]?.body as FormData).get("file") as File).name,
    );
    expect(names).toEqual(["inside.md", "plain.txt"]);
  });

  it("태그와 공개범위를 ZIP의 모든 문서 요청에 넣는다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const zip = new JSZip();
    zip.file("a.txt", "a");
    zip.file("b.md", "b");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [await archiveFile(zip)] },
    });
    await screen.findByText("a.txt — 대기");
    fireEvent.change(screen.getByLabelText("태그 (쉼표로 구분)"), {
      target: { value: "규정, 운영" },
    });
    fireEvent.click(screen.getByLabelText("비공개"));
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const call of fetchMock.mock.calls) {
      const body = call[1]?.body as FormData;
      expect(body.getAll("tags")).toEqual(["규정", "운영"]);
      expect(body.get("visibility")).toBe("private");
    }
  });

  it("ZIP에서 지원 문서가 두 개 이상 나오면 제목 입력을 숨긴다", async () => {
    const zip = new JSZip();
    zip.file("a.txt", "a");
    zip.file("b.md", "b");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [await archiveFile(zip)] },
    });

    await screen.findByText("2개 파일 선택됨");
    expect(screen.queryByLabelText("제목 (선택)")).toBeNull();
  });

  it("손상된 ZIP은 오류를 표시하고 요청하지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("업로드할 파일"), {
      target: { files: [new File([new Uint8Array([1, 2, 3])], "broken.zip")] },
    });

    expect(await screen.findByText("ZIP 파일을 열 수 없습니다.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "업로드" })).toBeDisabled();
  });

  it("드롭한 ZIP의 지원 문서를 업로드한다", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const zip = new JSZip();
    zip.file("docs/dropped.txt", "dropped");

    render(<UploadDropzone onUploaded={vi.fn()} />);
    fireEvent.drop(screen.getByText("파일을 끌어놓거나 클릭해 선택하세요."), {
      dataTransfer: { files: [await archiveFile(zip)] },
    });
    await screen.findByText("dropped.txt — 대기");
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(((fetchMock.mock.calls[0][1]?.body as FormData).get("file") as File).name).toBe(
      "dropped.txt",
    );
  });

});
