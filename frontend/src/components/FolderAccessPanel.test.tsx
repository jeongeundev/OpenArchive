import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Folder, FolderScope } from "@/lib/types";
import { FolderAccessPanel } from "./FolderAccessPanel";

const publicScope: FolderScope = { visibility: "public", users: [], groups: [] };
const root: Folder = { id: "f1", name: "RFP", parent_id: null, created_by: "kim", document_count: 0,
  scope: publicScope, inherited: false, can_manage: true, can_change_access: true };
const principals = { users: ["kim", "lee"], groups: ["사업팀", "개발팀"] };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(access: FolderScope, save: (body: FolderScope) => Response = body => json(body)) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/principals") return Promise.resolve(json(principals));
    if (url === "/api/folders/f1/access" && init?.method === "PUT")
      return Promise.resolve(save(JSON.parse(String(init.body)) as FolderScope));
    if (url === "/api/folders/f1/access") return Promise.resolve(json(access));
    return Promise.reject(new Error(`unexpected ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function putBodies(fetchMock: ReturnType<typeof stubFetch>): unknown[] {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

async function renderPanel(folder: Folder = root, onSaved = vi.fn()) {
  await act(async () => { render(<FolderAccessPanel folder={folder} onSaved={onSaved} />); });
  return onSaved;
}

describe("FolderAccessPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("새로 만든 최상위 폴더의 열람 범위는 「조직 공개」로 표시된다", async () => {
    stubFetch(publicScope);
    await renderPanel();
    expect(await screen.findByRole("radio", { name: "조직 공개" })).toBeChecked();
  });

  it("대상 없는 「제한」으로 저장하면 빈 대상을 PUT하고 갱신을 알린다", async () => {
    const fetchMock = stubFetch(publicScope);
    const onSaved = await renderPanel();
    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    expect(screen.getByText("대상을 고르지 않으면 폴더를 만든 사람만 봅니다")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));
    expect(await screen.findByText("열람 범위를 저장했습니다.")).toBeInTheDocument();
    expect(putBodies(fetchMock)).toEqual([{ visibility: "private", users: [], groups: [] }]);
    expect(onSaved).toHaveBeenCalled();
  });

  it("「제한」 + 그룹 「사업팀」을 저장한다", async () => {
    const fetchMock = stubFetch(publicScope);
    await renderPanel();
    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    fireEvent.change(await screen.findByLabelText("그룹 선택"), { target: { value: "사업팀" } });
    fireEvent.click(screen.getByRole("button", { name: "그룹 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));
    await waitFor(() => expect(putBodies(fetchMock)).toEqual([{ visibility: "private", users: [], groups: ["사업팀"] }]));
  });

  it("기존 부여에 그룹 「개발팀」을 더하면 전체 목록을 PUT한다", async () => {
    const fetchMock = stubFetch({ visibility: "private", users: [], groups: ["사업팀"] });
    await renderPanel();
    expect(await screen.findByRole("button", { name: "그룹 사업팀 제거" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("그룹 선택"), { target: { value: "개발팀" } });
    fireEvent.click(screen.getByRole("button", { name: "그룹 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));
    await waitFor(() => expect(putBodies(fetchMock)).toEqual([{ visibility: "private", users: [], groups: ["사업팀", "개발팀"] }]));
  });

  it("폴더 범위를 따르는 문서에 바로 적용됨을 알린다", async () => {
    stubFetch(publicScope);
    await renderPanel();
    expect(await screen.findByText("폴더 범위를 따르는 문서(다른 사람이 넣은 문서 포함)에 바로 적용됩니다")).toBeInTheDocument();
  });

  it("만들지 않은 사용자에게도 패널이 보이고, 저장하면 서버 거부 문구를 보인다", async () => {
    const other: Folder = { ...root, can_manage: false, can_change_access: false,
      scope: { visibility: "private", users: [], groups: ["사업팀"] } };
    const fetchMock = stubFetch(publicScope, () => json({ detail: "폴더를 관리할 권한이 없습니다." }, 403));
    await renderPanel(other);
    expect(screen.getByText("폴더를 만든 사람만 바꿀 수 있습니다")).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "제한" })).toBeChecked();
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/folders/f1/access" && init?.method !== "PUT")).toBe(false);
    const save = screen.getByRole("button", { name: "열람 범위 저장" });
    expect(save).toBeEnabled();
    fireEvent.click(screen.getByRole("radio", { name: "조직 공개" }));
    fireEvent.click(save);
    expect(await screen.findByRole("alert")).toHaveTextContent("폴더를 관리할 권한이 없습니다.");
    expect(putBodies(fetchMock)).toEqual([{ visibility: "public", users: [], groups: [] }]);
    expect(screen.getByRole("radio", { name: "조직 공개" })).toBeChecked();
  });

  it("「조직 공개」로 저장하면 대상을 비워 보낸다", async () => {
    const fetchMock = stubFetch({ visibility: "private", users: ["lee"], groups: ["사업팀"] });
    await renderPanel();
    fireEvent.click(await screen.findByRole("radio", { name: "조직 공개" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));
    await waitFor(() => expect(putBodies(fetchMock)).toEqual([{ visibility: "public", users: [], groups: [] }]));
  });

  it("하위 폴더에는 패널을 그리지 않는다", async () => {
    stubFetch(publicScope);
    await renderPanel({ ...root, id: "f2", parent_id: "f1", inherited: true });
    expect(screen.queryByRole("button", { name: "열람 범위 저장" })).not.toBeInTheDocument();
  });
});
