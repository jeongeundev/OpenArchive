import { describe, expect, it } from "vitest";

import { relationLabel } from "./relations";

describe("relationLabel", () => {
  it("broader 관계는 보는 방향에 따라 표현을 가른다", () => {
    expect(relationLabel("broader", "source")).toBe("더 자세한 문서");
    expect(relationLabel("broader", "target")).toBe("더 포괄적인 문서");
  });

  it("저장값 대신 사용자 어휘를 반환한다", () => {
    expect(relationLabel("overlaps")).toBe("전반적으로 같은 내용");
    expect(relationLabel("points_to")).toBe("이 대목에서 만난다");
    expect(relationLabel("related")).toBe("관련 있음");
  });
});
