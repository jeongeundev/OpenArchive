import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setCurrentUser } from "@/lib/user";
import { UploadDropzone } from "./UploadDropzone";

describe("UploadDropzone", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setCurrentUser("alice");
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

  it("데모 사용자가 저장돼 있어도 프리렌더 마크업 위에 하이드레이션할 수 있다", async () => {
    // `/`는 static prerender다. 서버에는 localStorage가 없으니 HTML은 항상 익명 상태로
    // 나오는데, 첫 클라이언트 렌더가 localStorage를 읽으면 트리가 달라져 불일치가 난다.
    vi.stubGlobal("fetch", vi.fn());
    localStorage.clear();
    const serverHtml = renderToString(<UploadDropzone onUploaded={vi.fn()} />);
    setCurrentUser("alice");

    const container = window.document.createElement("div");
    container.innerHTML = serverHtml;
    window.document.body.appendChild(container);
    const recoverableErrors: string[] = [];

    const root = await act(async () =>
      hydrateRoot(container, <UploadDropzone onUploaded={vi.fn()} />, {
        onRecoverableError: (error: unknown) => recoverableErrors.push(String(error)),
      }),
    );
    const textAfterHydration = container.textContent ?? "";
    await act(async () => root.unmount());
    container.remove();

    expect(recoverableErrors).toEqual([]);
    // 하이드레이션 후에는 저장된 사용자를 반영해 업로드가 열려야 한다.
    expect(textAfterHydration).not.toContain("사용자를 선택하면 업로드할 수 있습니다.");
  });

  it("익명이면 업로드를 비활성화하고 사용자를 선택하도록 안내한다", () => {
    localStorage.clear();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<UploadDropzone onUploaded={vi.fn()} />);

    expect(screen.getByRole("button", { name: "업로드" })).toBeDisabled();
    expect(screen.getByText("사용자를 선택하면 업로드할 수 있습니다.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
