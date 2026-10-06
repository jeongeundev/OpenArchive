import { describe, expect, it } from "vitest";
import { folderChildren, folderOptions, sameScope, scopeLabel } from "./folders";
import type { Folder } from "./types";
const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({id, name, parent_id, created_by: "kim", document_count: 0, scope: {visibility: "public", users: [], groups: []}, inherited: parent_id !== null, can_manage: true, can_change_access: parent_id === null});
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
