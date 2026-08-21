import { beforeEach, describe, expect, it } from "vitest";

import {
  clearPasswordChangedNotice,
  markPasswordChanged,
  wasPasswordChanged,
} from "./passwordChangeNotice";

describe("비밀번호 변경 안내", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("표시해 둔 안내를 다음 화면이 읽는다", () => {
    markPasswordChanged();

    expect(wasPasswordChanged()).toBe(true);
  });

  it("조회는 부수효과가 없다 — 두 번 읽어도 값이 그대로다", () => {
    markPasswordChanged();
    wasPasswordChanged();

    expect(wasPasswordChanged()).toBe(true);
  });

  it("지우면 다시 읽히지 않는다 — 새로고침이나 재방문에 다시 뜨지 않는다", () => {
    markPasswordChanged();
    clearPasswordChangedNotice();

    expect(wasPasswordChanged()).toBe(false);
  });

  it("표시해 두지 않았으면 읽히지 않는다", () => {
    expect(wasPasswordChanged()).toBe(false);
  });
});
