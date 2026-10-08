import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SiteFooter } from "./SiteFooter";

describe("SiteFooter", () => {
  it("한컴 HWP 형식 공개 조건의 고지 문구를 원문 그대로 보인다", () => {
    // 공개 조건이 UI에 적으라고 한 문장이다 (ADR-059 결정 1). 로그인 전 화면에도 보인다.
    render(<SiteFooter />);

    expect(
      screen.getByText(
        "본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.",
      ),
    ).toBeInTheDocument();
  });
});
