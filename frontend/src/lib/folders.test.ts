// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { afterEach, describe, expect, it, vi } from "vitest";
import { folderChildren, folderOptions, moveWithScopeConfirm, sameScope, scopeLabel } from "./folders";
import type { Folder } from "./types";
const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({id, name, parent_id, created_by: "kim", document_count: 0, scope: {visibility: "public", users: [], groups: []}, inherited: parent_id !== null, can_manage: true, can_change_access: parent_id === null, can_share: false});
export const folders = [folder("c", "채용", "b"), folder("a", "인사"), folder("d", "공고", "c"), folder("b", "업무", "a"), folder("e", "기획"), folder("orphan", "재무", "hidden")];
describe("folder labels", () => {
  it("orders parents before name-sorted children with full paths and treats missing parents as roots", () => {
    expect(folderOptions(folders).map(({id, label}) => [id, label])).toEqual([["e", "기획"], ["a", "인사"], ["b", "인사/업무"], ["c", "인사/업무/채용"], ["d", "인사/업무/채용/공고"], ["orphan", "재무"]]);
    expect(folderChildren([folder("z", "채용", "a"), folder("y", "업무", "a")], "a").map(f => f.id)).toEqual(["y", "z"]);
    expect(folderOptions(folders)[1].folder).toBe(folders[1]);
  });
  it("labels public, private and granted scopes", () => {
    expect(scopeLabel({visibility: "public", users: [], groups: []})).toBe("조직 공개");
    expect(scopeLabel({visibility: "private", users: [], groups: []})).toBe("제한");
    expect(scopeLabel({visibility: "private", users: ["kim"], groups: ["사업팀"]})).toBe("제한 · 사업팀, kim");
  });
  it("compares scopes independent of target order", () => {
    const a = {visibility: "private" as const, users: ["a", "b"], groups: ["x", "y"]};
    expect(sameScope(a, {...a, users: ["b", "a"], groups: ["y", "x"]})).toBe(true);
    expect(sameScope(a, {...a, visibility: "public"})).toBe(false);
    expect(sameScope(a, {...a, users: ["a"]})).toBe(false);
    expect(sameScope(a, {...a, groups: ["x", "z"]})).toBe(false);
  });
});

describe("moveWithScopeConfirm", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("목록에 없는 폴더로는 범위를 알 수 없으니 옮기지 않는다 — 확인 없이 범위가 바뀌는 것을 막는다", async () => {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/access")
      ? { follows_folder: true, folder: null, folder_scope: null, hidden_folder: false,
        visibility: "private", users: [], groups: [] }
      : {}), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm");

    await expect(moveWithScopeConfirm("d1", "not-loaded", [])).rejects.toThrow("폴더 목록을 불러오지 못했습니다.");
    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toEqual([]);
  });
});
