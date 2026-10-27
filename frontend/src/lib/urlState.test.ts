// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { afterEach, describe, expect, it, vi } from "vitest";

import { MAX_K } from "./types";
import {
  lastListHref,
  listHref,
  listStateFromParams,
  rememberListHref,
  searchHref,
  searchInputFromParams,
} from "./urlState";

describe("검색 조건 주소", () => {
  it("조건을 주소로 쓰고 같은 조건으로 읽는다", () => {
    const input = { query: "연차 휴가", tags: ["인사", "복무"], contentType: "pdf" as const, folderId: "f-hr", k: 20 };
    const href = searchHref(input);
    expect(href.startsWith("/search?")).toBe(true);
    expect(searchInputFromParams(new URL(href, "http://localhost").searchParams)).toEqual(input);
  });

  it("기본값은 주소에 넣지 않는다", () => {
    expect(searchHref({ query: "출장", tags: [], contentType: null, folderId: null, k: 10 })).toBe("/search?q=%EC%B6%9C%EC%9E%A5");
  });

  it("검색어가 없으면 복원할 검색이 없다", () => {
    expect(searchInputFromParams(new URLSearchParams("tags=인사"))).toBeNull();
    expect(searchInputFromParams(new URLSearchParams("q=%20%20"))).toBeNull();
  });

  it("손으로 고친 주소의 유형·결과 수는 화면이 보낼 수 있는 값으로 되돌린다", () => {
    const input = searchInputFromParams(new URLSearchParams(`q=a&type=exe&k=${MAX_K + 5}`));
    expect(input).toMatchObject({ contentType: null, k: MAX_K });
    expect(searchInputFromParams(new URLSearchParams("q=a&k=abc"))).toMatchObject({ k: 10 });
    expect(searchInputFromParams(new URLSearchParams("q=a&k=0"))).toMatchObject({ k: 1 });
  });
});

describe("문서 목록 주소", () => {
  it("폴더·조건·쪽을 주소로 쓰고 같은 상태로 읽는다", () => {
    const state = { folderId: "f-hr", filters: { q: "출장", contentType: "hwp" as const, tag: "보안", sort: "title" as const }, page: 2 };
    const href = listHref(state);
    expect(listStateFromParams(new URL(href, "http://localhost").searchParams)).toEqual(state);
  });

  it("기본 상태는 「/」이고 쪽은 1부터 센다", () => {
    expect(listHref({ folderId: null, filters: { sort: "updated" }, page: 0 })).toBe("/");
    expect(listHref({ folderId: null, filters: { sort: "updated" }, page: 1 })).toBe("/?page=2");
    expect(listStateFromParams(new URLSearchParams())).toEqual({ folderId: null, filters: { sort: "updated" }, page: 0 });
  });

  it("「부여받은 문서」 보기는 `granted=1`로 쓰고 읽으며 폴더와 함께 쓰지 않는다 (D20)", () => {
    expect(listStateFromParams(new URLSearchParams("granted=1"))).toEqual({ folderId: null, filters: { sort: "updated" }, page: 0, granted: true });
    expect(listHref({ folderId: null, filters: { sort: "updated" }, page: 0, granted: true })).toBe("/?granted=1");
    expect(listHref({ folderId: "f-hr", filters: { sort: "updated" }, page: 0, granted: true })).toBe("/?granted=1");
    expect(listStateFromParams(new URLSearchParams("granted=1&folder=f-hr")).folderId).toBeNull();
    const state = { folderId: null, filters: { tag: "보안", sort: "title" as const }, page: 1, granted: true };
    expect(listStateFromParams(new URL(listHref(state), "http://localhost").searchParams)).toEqual(state);
  });

  it("손으로 고친 주소의 쪽·유형·정렬은 기본값으로 되돌린다", () => {
    for (const page of ["0", "-1", "1.5", "abc"]) {
      expect(listStateFromParams(new URLSearchParams(`page=${page}`)).page).toBe(0);
    }
    expect(listStateFromParams(new URLSearchParams("type=exe&sort=size")).filters).toEqual({ sort: "updated" });
  });
});

describe("마지막 목록 주소", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("기억한 목록 주소를 돌려주고, 없으면 「/」다", () => {
    expect(lastListHref()).toBe("/");
    rememberListHref("/?sort=title&page=2");
    expect(lastListHref()).toBe("/?sort=title&page=2");
  });

  it("저장소를 쓸 수 없어도 「/」로 동작한다", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => rememberListHref("/?page=2")).not.toThrow();
    expect(lastListHref()).toBe("/");
  });

  it("목록 주소가 아닌 값은 쓰지 않는다", () => {
    sessionStorage.setItem("openarchive:last-list", "https://evil.example/");
    expect(lastListHref()).toBe("/");
  });
});
