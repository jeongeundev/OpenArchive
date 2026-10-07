import { describe, expect, it } from "vitest";

import { expiresAtFromDate, formatExpiry } from "./tokenExpiry";

// 기대값을 로컬 시간대의 Date로 만들어, 테스트를 돌리는 머신의 TZ와 무관하게 결정적이다.
describe("expiresAtFromDate", () => {
  it("비우면 만료 없음(null)이다", () => {
    expect(expiresAtFromDate("")).toBeNull();
  });

  it("고른 날짜는 그날 끝까지 유효하다 — 다음 날 로컬 00:00을 보낸다", () => {
    expect(expiresAtFromDate("2026-10-31")).toBe(new Date(2026, 10, 1).toISOString());
  });

  it("연말을 넘긴다", () => {
    expect(expiresAtFromDate("2026-12-31")).toBe(new Date(2027, 0, 1).toISOString());
  });

  it("평범한 날짜도 다음 날 자정이다", () => {
    expect(expiresAtFromDate("2026-02-28")).toBe(new Date(2026, 2, 1).toISOString());
  });
});

describe("formatExpiry", () => {
  it("만료가 없으면 「없음」", () => {
    expect(formatExpiry(null)).toBe("없음");
  });

  it("로컬 자정 정각이면 전날 날짜까지로 보인다 — 날짜 입력과 맞는다", () => {
    expect(formatExpiry(new Date(2026, 10, 1).toISOString())).toBe("2026-10-31까지");
  });

  it("자정이 아니면(API로 분 단위 발급) 시:분까지 보인다", () => {
    expect(formatExpiry(new Date(2026, 9, 8, 14, 5).toISOString())).toBe("2026-10-08 14:05까지");
  });
});
