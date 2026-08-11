import { describe, expect, it } from "vitest";

import { relationLabel } from "./relations";

describe("relationLabel", () => {
  it("broader 관계는 보는 방향에 따라 표현을 가른다", () => {
    expect(relationLabel("broader", "source")).toBe("더 자세한 문서");
    expect(relationLabel("broader", "target")).toBe("더 포괄적인 문서");
  });

  it("저장값 대신 사용자 어휘를 반환한다", () => {
    expect(relationLabel("overlaps")).toBe("여러 대목에서 만난다");
    expect(relationLabel("points_to")).toBe("이 대목에서 만난다");
    expect(relationLabel("related")).toBe("관련 있음");
  });

  // refers는 사람이 본문에 [[제목]]으로 직접 쓴 링크다. 어휘가 없으면 "관련 있음"으로
  // 떨어져 자동 판정과 구분되지 않는다.
  it("본문 위키링크로 이어진 관계를 자동 판정과 다른 어휘로 말한다", () => {
    expect(relationLabel("refers")).toBe("본문에서 가리킨다");
    expect(relationLabel("refers")).not.toBe(relationLabel("related"));
  });
});
