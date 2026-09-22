import { describe, expect, it } from "vitest";

import { parseWikilink } from "./wikilink";

describe("parseWikilink", () => {
  it("별칭과 절을 제거한 제목으로 해석하고 별칭을 표시한다", () => {
    expect(parseWikilink("", "기본 서식 구문#목록|목록")).toEqual({
      title: "기본 서식 구문",
      label: "목록",
    });
  });

  it("경로의 마지막 제목을 해석하고 별칭이 없으면 원문 표기를 유지한다", () => {
    expect(parseWikilink("", "Obsidian Web Clipper/템플릿|템플릿")).toEqual({
      title: "템플릿",
      label: "템플릿",
    });
    expect(parseWikilink("", "폴더/제목")).toEqual({
      title: "제목",
      label: "폴더/제목",
    });
  });

  it("절을 제거한 제목으로 해석하고 별칭이 없으면 절이 포함된 원문을 표시한다", () => {
    expect(parseWikilink("", "내부 링크#헤딩")).toEqual({
      title: "내부 링크",
      label: "내부 링크#헤딩",
    });
  });

  it.each([
    ["!", "첨부.png"],
    ["!", "노트"],
    ["", "그림.JPG"],
    ["", "#절만"],
    ["", "|별칭만"],
    ["", '""'],
  ])("링크가 아닌 대상 bang=%s raw=%s를 제외한다", (bang, raw) => {
    expect(parseWikilink(bang, raw)).toBeNull();
  });

  it("문서 형식 확장자는 링크 대상으로 유지한다", () => {
    expect(parseWikilink("", "규정집.pdf")).toEqual({
      title: "규정집.pdf",
      label: "규정집.pdf",
    });
  });

  it("대상의 양끝 공백을 제거한다", () => {
    expect(parseWikilink("", " 제목 ")).toEqual({
      title: "제목",
      label: "제목",
    });
  });
});
