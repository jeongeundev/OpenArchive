import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Folder } from "@/lib/types";
import { FolderShareToggles } from "./FolderShareToggles";

const scope = { visibility: "public" as const, users: [], groups: [] };
const child: Folder = { id: "f-2", name: "채용", parent_id: "f-1", created_by: "park", document_count: 0, scope,
  inherited: true, can_manage: false, can_change_access: false, can_share: true };

const shares = [
  { id: "s-1", name: "B사 협업", created_at: "2026-10-02T00:00:00Z", documents: [], folders: [{ id: "f-2", name: "채용" }], tokens: [] },
  { id: "s-2", name: "C사", created_at: "2026-10-02T00:00:00Z", documents: [], folders: [], tokens: [] },
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function setup(route: (url: string, method: string) => Response | undefined = () => undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const custom = route(url, method);
    if (custom !== undefined) return custom;
    if (url === "/api/shares") return json(shares);
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("FolderShareToggles", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("공유 목록을 보이고 이미 든 공유는 켜진 상태다", async () => {
    setup();
    await act(async () => { render(<FolderShareToggles folder={child} />); });
    expect(await screen.findByRole("checkbox", { name: "B사 협업" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "C사" })).not.toBeChecked();
    expect(screen.getByText(/하위 폴더의 「폴더 범위 따름」 문서가 소유자와 관계없이/)).toBeInTheDocument();
    expect(screen.getByText(/「개별 지정」 문서는 빠집니다/)).toBeInTheDocument();
  });

  it("토글하면 폴더를 공유에 넣고 뺀다", async () => {
    const fetchMock = setup();
    await act(async () => { render(<FolderShareToggles folder={child} />); });
    fireEvent.click(await screen.findByRole("checkbox", { name: "C사" }));
    await waitFor(() => expect(screen.getByText("「C사」 공유에 넣었습니다.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "B사 협업" }));
    await waitFor(() => expect(screen.getByText("「B사 협업」 공유에서 뺐습니다.")).toBeInTheDocument());
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT" || init?.method === "DELETE")
      .map(([url, init]) => [String(url), init?.method]);
    expect(writes).toEqual([["/api/shares/s-2/folders/f-2", "PUT"], ["/api/shares/s-1/folders/f-2", "DELETE"]]);
  });

  it("거부되면 서버 메시지를 보이고 체크를 되돌린다", async () => {
    setup((url, method) => method === "PUT" ? json({ detail: "폴더를 관리할 권한이 없습니다." }, 403) : undefined);
    await act(async () => { render(<FolderShareToggles folder={child} />); });
    const box = await screen.findByRole("checkbox", { name: "C사" });
    fireEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("폴더를 관리할 권한이 없습니다.");
    expect(box).not.toBeChecked();
  });

  it("공유가 없으면 설정 화면으로 안내한다", async () => {
    setup(url => url === "/api/shares" ? json([]) : undefined);
    await act(async () => { render(<FolderShareToggles folder={child} />); });
    expect(await screen.findByText(/외부 공유가 없습니다/)).toBeInTheDocument();
  });
});
