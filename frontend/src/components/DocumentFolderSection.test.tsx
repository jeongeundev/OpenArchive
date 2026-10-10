import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentAccess, DocumentFolder, Folder, FolderScope } from "@/lib/types";
import { DocumentFolderSection } from "./DocumentFolderSection";

const publicScope: FolderScope = { visibility: "public", users: [], groups: [] };
const bizScope: FolderScope = { visibility: "private", users: [], groups: ["사업팀"] };
const insa: Folder = { id: "a", name: "인사", parent_id: null, created_by: "alice", document_count: 1, scope: publicScope,
  inherited: false, can_manage: true, can_change_access: true, can_share: false };
const hire: Folder = { id: "b", name: "채용", parent_id: "a", created_by: "alice", document_count: 0, scope: publicScope,
  inherited: true, can_manage: true, can_change_access: false, can_share: false };
const rfp: Folder = { id: "c", name: "RFP", parent_id: null, created_by: "kim", document_count: 0, scope: bizScope,
  inherited: false, can_manage: false, can_change_access: false, can_share: false };
const inInsa: DocumentFolder = { id: "a", name: "인사", path: [{ id: "a", name: "인사" }] };

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function stub(access: Partial<DocumentAccess> = {}) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/folders") return json([insa, hire, rfp]);
    if (url.endsWith("/access")) {
      return json({ follows_folder: true, folder: inInsa, folder_scope: publicScope, visibility: "private",
        users: [], groups: [], ...access });
    }
    if (url.endsWith("/folder") && init?.method === "PUT") return json({});
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function moves(fetchMock: ReturnType<typeof stub>) {
  return fetchMock.mock.calls.filter(([url, init]) => url.endsWith("/folder") && init?.method === "PUT")
    .map(([url, init]) => [url, JSON.parse(String(init?.body))]);
}

async function choose(folderId: string) {
  await screen.findByRole("option", { name: "인사/채용" });
  fireEvent.change(screen.getByLabelText("폴더"), { target: { value: folderId } });
  fireEvent.click(screen.getByRole("button", { name: "옮기기" }));
}

describe("DocumentFolderSection", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("폴더 경로의 각 조각을 그 폴더 목록으로 링크한다", () => {
    stub();
    const folder: DocumentFolder = { id: "b", name: "채용", path: [{ id: "a", name: "인사" }, { id: "b", name: "채용" }] };
    render(<DocumentFolderSection documentId="d1" folder={folder} isOwner={false} onMoved={vi.fn()} />);
    expect(screen.getByRole("link", { name: "인사" })).toHaveAttribute("href", "/?folder=a");
    expect(screen.getByRole("link", { name: "채용" })).toHaveAttribute("href", "/?folder=b");
  });

  it("폴더를 볼 수 없는 비소유자에게는 아무것도 그리지 않는다", () => {
    const fetchMock = stub();
    const { container } = render(<DocumentFolderSection documentId="d1" folder={null} isOwner={false} onMoved={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/폴더/)).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("비소유자에게는 이동 컨트롤이 없고 열람 범위도 조회하지 않는다", () => {
    const fetchMock = stub();
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner={false} onMoved={vi.fn()} />);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "옮기기" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("범위가 같은 폴더(인사/채용)로는 확인 없이 옮긴다", async () => {
    const fetchMock = stub();
    const confirm = vi.spyOn(window, "confirm");
    const onMoved = vi.fn();
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={onMoved} />);
    await choose("b");
    await waitFor(() => expect(onMoved).toHaveBeenCalled());
    expect(confirm).not.toHaveBeenCalled();
    expect(moves(fetchMock)).toEqual([["/api/documents/d1/folder", { folder_id: "b" }]]);
  });

  it("범위가 다른 폴더로 옮기면 확인 안내를 띄우고, 승인하면 옮긴다", async () => {
    const fetchMock = stub();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onMoved = vi.fn();
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={onMoved} />);
    await choose("c");
    await waitFor(() => expect(onMoved).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith("열람 범위가 바뀝니다: 「조직 공개」 → 「제한 · 사업팀」. 옮기시겠습니까?");
    expect(moves(fetchMock)).toEqual([["/api/documents/d1/folder", { folder_id: "c" }]]);
  });

  it("확인을 취소하면 옮기지 않는다", async () => {
    const fetchMock = stub();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={vi.fn()} />);
    await choose("c");
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(moves(fetchMock)).toEqual([]);
  });

  it("폴더 밖으로 옮기면 이동 후 범위는 문서 자신의 범위다", async () => {
    stub();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={vi.fn()} />);
    await screen.findByRole("option", { name: "인사/채용" });
    fireEvent.change(screen.getByLabelText("폴더"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "옮기기" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(
      "열람 범위가 바뀝니다: 「조직 공개」 → 「제한」. 옮기시겠습니까?"));
  });

  it("개별 지정 문서는 범위가 다른 폴더로도 확인 없이 옮긴다", async () => {
    const fetchMock = stub({ follows_folder: false });
    const confirm = vi.spyOn(window, "confirm");
    const onMoved = vi.fn();
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={onMoved} />);
    await choose("c");
    await waitFor(() => expect(onMoved).toHaveBeenCalled());
    expect(confirm).not.toHaveBeenCalled();
    expect(moves(fetchMock)).toEqual([["/api/documents/d1/folder", { folder_id: "c" }]]);
  });

  const hiddenAccess = { folder: null, folder_scope: null, hidden_folder: true };

  it("볼 수 없는 폴더 안 자기 문서는 이름 없이 알리고, 폴더 밖으로 뺄 수 있다", async () => {
    const fetchMock = stub(hiddenAccess);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onMoved = vi.fn();
    render(<DocumentFolderSection documentId="d1" folder={null} hiddenFolder isOwner onMoved={onMoved} />);
    expect(screen.getByText("볼 수 없는 폴더")).toBeInTheDocument();
    await screen.findByRole("option", { name: "인사/채용" });
    expect(screen.getByLabelText("폴더")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "옮기기" }));
    await waitFor(() => expect(onMoved).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith(
      "열람 범위가 바뀝니다: 「볼 수 없는 폴더의 범위」 → 「제한」. 옮기시겠습니까?");
    expect(moves(fetchMock)).toEqual([["/api/documents/d1/folder", { folder_id: null }]]);
  });

  it("볼 수 없는 폴더 안 개별 지정 문서는 확인 없이 옮긴다", async () => {
    const fetchMock = stub({ ...hiddenAccess, follows_folder: false });
    const confirm = vi.spyOn(window, "confirm");
    const onMoved = vi.fn();
    render(<DocumentFolderSection documentId="d1" folder={null} hiddenFolder isOwner onMoved={onMoved} />);
    await choose("c");
    await waitFor(() => expect(onMoved).toHaveBeenCalled());
    expect(confirm).not.toHaveBeenCalled();
    expect(moves(fetchMock)).toEqual([["/api/documents/d1/folder", { folder_id: "c" }]]);
  });

  it("이동이 거부되면 서버 문구를 보인다", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/folders") return json([insa, hire, rfp]);
      if (url.endsWith("/access")) return json({ follows_folder: true, folder: inInsa, folder_scope: publicScope,
        visibility: "private", users: [], groups: [] });
      if (init?.method === "PUT") return json({ detail: "폴더를 찾을 수 없습니다." }, 404);
      return json({});
    }));
    render(<DocumentFolderSection documentId="d1" folder={inInsa} isOwner onMoved={vi.fn()} />);
    await choose("b");
    expect(await screen.findByRole("alert")).toHaveTextContent("폴더를 찾을 수 없습니다.");
  });
});
