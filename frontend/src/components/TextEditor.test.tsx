import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetail } from "@/lib/types";
import { setCurrentUser } from "@/lib/user";
import { TextEditor } from "./TextEditor";

const document: DocumentDetail = {
  id: "document-1",
  title: "OpenSQL 운영 가이드",
  filename: "guide.pdf",
  content_type: "pdf",
  content: "추출된 텍스트",
  version: 2,
  owner_id: "alice",
  visibility: "public",
  tags: [],
  embedding_status: "ready",
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T11:00:00Z",
  versions: [],
  chunk_count: 1,
  chunk_version: 2,
};

const notice =
  "원본 파일이 아니라 업로드 시 추출된 텍스트를 편집합니다. 저장하면 새 텍스트 버전이 만들어집니다.";

describe("TextEditor", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setCurrentUser("alice");
  });

  it("PDF와 DOCX에는 추출 텍스트 편집 안내를 상시 표시한다", () => {
    const { rerender } = render(
      <TextEditor
        disabled={false}
        document={document}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByText(notice)).toBeInTheDocument();

    rerender(
      <TextEditor
        disabled={false}
        document={{ ...document, content_type: "txt" }}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.queryByText(notice)).not.toBeInTheDocument();
  });

  it("편집한 추출 텍스트와 화면이 읽은 버전을 저장한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ...document, content: "고친 텍스트", version: 3 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TextEditor
        disabled={false}
        document={document}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    fireEvent.change(screen.getByRole("textbox", { name: "추출 텍스트" }), {
      target: { value: "고친 텍스트" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      content: "고친 텍스트",
      version: 2,
    });
  });

  it("편집 중 폴링으로 문서가 갱신돼도 편집을 시작한 버전을 보낸다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ...document, version: 4 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <TextEditor
        disabled={false}
        document={document}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    fireEvent.change(screen.getByRole("textbox", { name: "추출 텍스트" }), {
      target: { value: "내가 고친 텍스트" },
    });

    // 조건부 폴링이 다른 사용자의 저장 결과(v3)를 가져온 상황.
    rerender(
      <TextEditor
        disabled={false}
        document={{ ...document, content: "남이 고친 텍스트", version: 3 }}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    // v3을 보내면 서버가 충돌을 감지하지 못해 남의 변경을 덮어쓴다.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      content: "내가 고친 텍스트",
      version: 2,
    });
  });

  it("409 충돌 뒤에도 사용자가 입력한 내용을 보존한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
            current_version: 3,
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(
      <TextEditor
        disabled={false}
        document={document}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    const textarea = screen.getByRole("textbox", { name: "추출 텍스트" });
    fireEvent.change(textarea, { target: { value: "보존할 편집 내용" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(
      await screen.findByText(
        "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요. 현재 서버 버전: v3",
      ),
    ).toBeInTheDocument();
    expect(textarea).toHaveValue("보존할 편집 내용");
  });

  it("저장 성공 후 상세 재조회를 요청한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ...document, version: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const onSaved = vi.fn();

    render(
      <TextEditor
        disabled={false}
        document={document}
        onEditingChange={vi.fn()}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(screen.queryByRole("textbox", { name: "추출 텍스트" })).not.toBeInTheDocument();
  });

  it("익명이면 편집 요청을 보내지 못하게 한다", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TextEditor
        disabled
        document={document}
        onEditingChange={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "편집" })).toBeDisabled();
    expect(screen.getByText("사용자를 선택하면 편집할 수 있습니다.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
