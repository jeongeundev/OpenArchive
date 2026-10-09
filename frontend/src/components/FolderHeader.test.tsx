import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Folder } from "@/lib/types";
import { FolderHeader } from "./FolderHeader";

const scope = { visibility: "public" as const, users: [], groups: [] };
const root: Folder = { id: "a", name: "인사", parent_id: null, created_by: "kim", document_count: 0, scope,
  inherited: false, can_manage: true, can_change_access: true };
const child: Folder = { id: "b", name: "채용", parent_id: "a", created_by: "kim", document_count: 0, scope,
  inherited: true, can_manage: true, can_change_access: false };

function respond(status: number, body?: unknown) {
  return Promise.resolve(body === undefined ? new Response(null, { status })
    : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("FolderHeader", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("최상위 폴더는 경로와 「조직 공개」를, 하위 폴더는 「상위 폴더 범위 따름」을 표시한다", () => {
    const { rerender } = render(<FolderHeader folder={root} folders={[root, child]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "인사" })).toBeInTheDocument();
    expect(screen.getByText("조직 공개")).toBeInTheDocument();
    rerender(<FolderHeader folder={child} folders={[root, child]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "인사/채용" })).toBeInTheDocument();
    expect(screen.getByText("상위 폴더 범위 따름(조직 공개)")).toBeInTheDocument();
  });

  it("제한 폴더의 범위는 대상과 함께 표시한다", () => {
    const limited = { ...root, scope: { visibility: "private" as const, users: ["lee"], groups: ["사업팀"] } };
    render(<FolderHeader folder={limited} folders={[limited]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.getByText("제한 · 사업팀, lee")).toBeInTheDocument();
  });

  it("「하위 폴더」로 그 폴더 아래 폴더 생성을 요청한다", async () => {
    const fetchMock = vi.fn(() => respond(201, { ...child, id: "c", name: "평가" }));
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    render(<FolderHeader folder={root} folders={[root]} onChanged={onChanged} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "하위 폴더" }));
    fireEvent.change(screen.getByLabelText("하위 폴더 이름"), { target: { value: "평가" } });
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/folders");
    expect(JSON.parse(String(init.body))).toEqual({ name: "평가", parent_id: "a" });
  });

  it("이름을 바꾸면 PATCH 후 갱신을 알린다", async () => {
    const fetchMock = vi.fn(() => respond(200, { ...root, name: "인사팀" }));
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    render(<FolderHeader folder={root} folders={[root]} onChanged={onChanged} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "이름 변경" }));
    const input = screen.getByLabelText("폴더 이름");
    expect(input).toHaveValue("인사");
    fireEvent.change(input, { target: { value: "인사팀" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/folders/a");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ name: "인사팀" });
  });

  it("문서가 든 폴더를 삭제하면 「폴더가 비어 있지 않습니다.」로 거부된다", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", vi.fn(() => respond(409, { detail: "폴더가 비어 있지 않습니다." })));
    const onDeleted = vi.fn();
    render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("폴더가 비어 있지 않습니다.");
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("빈 폴더를 삭제하면 삭제를 알린다", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(() => respond(204));
    vi.stubGlobal("fetch", fetchMock);
    const onDeleted = vi.fn();
    render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/folders/a");
    expect(init.method).toBe("DELETE");
  });

  it("삭제 확인 문구는 폴더 이름의 받침과 무관하게 맞는 형태다", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { rerender } = render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(confirm).toHaveBeenLastCalledWith("「인사」 폴더를 삭제하시겠습니까? 빈 폴더만 삭제할 수 있습니다.");
    const withFinal = { ...root, name: "채용" };
    rerender(<FolderHeader folder={withFinal} folders={[withFinal]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(confirm).toHaveBeenLastCalledWith("「채용」 폴더를 삭제하시겠습니까? 빈 폴더만 삭제할 수 있습니다.");
  });

  it("삭제 확인을 취소하면 요청하지 않는다", () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("다른 사용자가 만든 폴더도 버튼은 보이고, 시도하면 서버의 거부 문구를 보인다", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", vi.fn(() => respond(403, { detail: "폴더를 관리할 권한이 없습니다." })));
    const others = { ...root, created_by: "park", can_manage: false, can_change_access: false };
    render(<FolderHeader folder={others} folders={[others]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.getByText("폴더를 만든 사람만 바꿀 수 있습니다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이름 변경" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("폴더를 관리할 권한이 없습니다.");
    fireEvent.click(screen.getByRole("button", { name: "이름 변경" }));
    fireEvent.change(screen.getByLabelText("폴더 이름"), { target: { value: "바꿈" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some(el => el.textContent?.includes("폴더를 관리할 권한이 없습니다."))).toBe(true));
  });

  it("만든 사람이면 권한 안내를 보이지 않는다", () => {
    render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.queryByText("폴더를 만든 사람만 바꿀 수 있습니다")).not.toBeInTheDocument();
  });
  it("최상위 폴더는 「열람 범위」로 패널을 펼치고 저장 뒤 갱신을 알리며, 하위 폴더에는 버튼이 없다", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/principals") return respond(200, { users: [], groups: [] });
      if (url === "/api/folders/a/access" && init?.method === "PUT") return respond(200, JSON.parse(String(init.body)));
      if (url === "/api/folders/a/access") return respond(200, scope);
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onAccessSaved = vi.fn();
    const { rerender } = render(<FolderHeader folder={root} folders={[root, child]} onChanged={vi.fn()}
      onDeleted={vi.fn()} onAccessSaved={onAccessSaved} />);
    expect(screen.queryByRole("button", { name: "열람 범위 저장" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "열람 범위" }));
    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));
    await waitFor(() => expect(onAccessSaved).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "열람 범위" }));
    expect(screen.queryByRole("button", { name: "열람 범위 저장" })).not.toBeInTheDocument();
    rerender(<FolderHeader folder={child} folders={[root, child]} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "열람 범위" })).not.toBeInTheDocument();
  });
});


it.each([root, child])("폴더 $id 이전 버튼은 권한 없이도 표시되고 저장 후 갱신한다", async folder => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const fetchMock = vi.fn((url: string) => respond(200, url === "/api/principals" ? { users: ["kim", "lee"], groups: [] } : { created_by: "lee", still_visible: true }));
  vi.stubGlobal("fetch", fetchMock);
  const onChanged = vi.fn();
  render(<FolderHeader folder={{ ...folder, can_manage: false }} folders={[root, child]} onChanged={onChanged} onDeleted={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "소유자 이전" }));
  await screen.findByRole("option", { name: "lee" });
  fireEvent.change(screen.getByLabelText("이전받을 사용자"), { target: { value: "lee" } });
  fireEvent.click(screen.getByRole("button", { name: "이전" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(fetchMock).toHaveBeenCalledWith(`/api/folders/${folder.id}/owner`, expect.objectContaining({ method: "PUT", body: JSON.stringify({ owner: "lee" }) }));
  vi.unstubAllGlobals(); vi.restoreAllMocks();
});
it.each([403, 200])("폴더 이전 결과 %s는 거부 또는 선택 해제를 반영한다", async status => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn((url: string) => respond(url === "/api/principals" ? 200 : status, url === "/api/principals" ? { users: ["lee"], groups: [] } : status === 403 ? { detail: "폴더를 관리할 권한이 없습니다." } : { still_visible: false })));
  const onDeleted = vi.fn();
  render(<FolderHeader folder={root} folders={[root]} onChanged={vi.fn()} onDeleted={onDeleted} />);
  fireEvent.click(screen.getByRole("button", { name: "소유자 이전" }));
  await screen.findByRole("option", { name: "lee" });
  fireEvent.change(screen.getByLabelText("이전받을 사용자"), { target: { value: "lee" } });
  fireEvent.click(screen.getByRole("button", { name: "이전" }));
  if (status === 403) expect(await screen.findByRole("alert")).toHaveTextContent("폴더를 관리할 권한이 없습니다.");
  else await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  vi.unstubAllGlobals(); vi.restoreAllMocks();
});
