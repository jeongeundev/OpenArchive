import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TrashPage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const items = [
  {
    id: "doc-1",
    title: "인사 규정",
    deleted_at: "2026-10-05T09:30:00Z",
    purge_at: "2026-11-04T09:30:00Z",
  },
  {
    id: "doc-2",
    title: "보안 지침",
    deleted_at: "2026-10-06T09:30:00Z",
    purge_at: "2026-11-05T09:30:00Z",
  },
];

/** 경로·메서드로 응답을 고르고 요청을 모은다. */
function routedFetch(list: unknown[]) {
  const calls: { url: string; method: string }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url === "/api/documents/trash") return response(list);
    if (url.endsWith("/restore") && method === "POST") return response({});
    if (method === "DELETE") return new Response(null, { status: 204 });
    return response({ detail: "없음" }, 404);
  });
  return { fetchMock, calls };
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  if (row === null) throw new Error(`${title} 행이 없다`);
  return row;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("휴지통 화면", () => {
  it("내가 지운 문서를 제목·삭제 일시·영구 삭제 예정일과 함께 보여 준다", async () => {
    vi.stubGlobal("fetch", routedFetch(items).fetchMock);

    render(<TrashPage />);

    await screen.findByText("인사 규정");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["제목", "삭제 일시", "영구 삭제 예정일", ""]);
    const row = rowOf("인사 규정");
    expect(row.querySelectorAll("time")).toHaveLength(2);
    expect(row.querySelectorAll("time")[0].getAttribute("datetime")).toBe("2026-10-05T09:30:00Z");
    expect(row.querySelectorAll("time")[1].getAttribute("datetime")).toBe("2026-11-04T09:30:00Z");
    // 휴지통 문서의 상세는 404다 — 링크를 걸지 않는다.
    expect(within(row).queryByRole("link")).toBeNull();
    expect(screen.getByText("보안 지침")).toBeInTheDocument();
  });

  it("비어 있으면 빈 상태를 알린다", async () => {
    vi.stubGlobal("fetch", routedFetch([]).fetchMock);

    render(<TrashPage />);

    expect(await screen.findByText("휴지통이 비어 있습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("「복원」을 누르면 복원을 요청하고 목록에서 뺀다", async () => {
    const { fetchMock, calls } = routedFetch(items);
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);
    await screen.findByText("인사 규정");
    fireEvent.click(within(rowOf("인사 규정")).getByRole("button", { name: "복원" }));

    await waitFor(() => expect(screen.queryByText("인사 규정")).toBeNull());
    expect(calls).toContainEqual({ url: "/api/documents/doc-1/restore", method: "POST" });
    expect(screen.getByText("보안 지침")).toBeInTheDocument();
  });

  it("「영구 삭제」는 되돌릴 수 없다고 확인한 뒤에만 지운다", async () => {
    const { fetchMock, calls } = routedFetch(items);
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<TrashPage />);
    await screen.findByText("인사 규정");
    fireEvent.click(within(rowOf("인사 규정")).getByRole("button", { name: "영구 삭제" }));

    expect(confirm.mock.calls[0][0]).toContain("삭제하면 되돌릴 수 없습니다");
    await waitFor(() => expect(screen.queryByText("인사 규정")).toBeNull());
    expect(calls).toContainEqual({ url: "/api/documents/doc-1?permanent=true", method: "DELETE" });
  });

  it("영구 삭제 확인창에서 취소하면 아무것도 요청하지 않는다", async () => {
    const { fetchMock, calls } = routedFetch(items);
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<TrashPage />);
    await screen.findByText("인사 규정");
    fireEvent.click(within(rowOf("인사 규정")).getByRole("button", { name: "영구 삭제" }));

    expect(calls).toEqual([{ url: "/api/documents/trash", method: "GET" }]);
    expect(screen.getByText("인사 규정")).toBeInTheDocument();
  });

  it("실패하면 서버 문구를 보이고 문서를 목록에 남긴다", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input) === "/api/documents/trash"
        ? response(items)
        : response({ detail: "문서를 찾을 수 없습니다." }, 404)));

    render(<TrashPage />);
    await screen.findByText("인사 규정");
    fireEvent.click(within(rowOf("인사 규정")).getByRole("button", { name: "복원" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("문서를 찾을 수 없습니다.");
    expect(screen.getByText("인사 규정")).toBeInTheDocument();
  });
});
