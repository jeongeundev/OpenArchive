import { beforeEach, describe, expect, it } from "vitest";

import { DEMO_USERS, getCurrentUser, setCurrentUser } from "./user";

describe("demo user storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores and returns a selected demo user", () => {
    setCurrentUser(DEMO_USERS[0]);

    expect(getCurrentUser()).toBe(DEMO_USERS[0]);
  });

  it("removes the stored user when switching to anonymous", () => {
    setCurrentUser(DEMO_USERS[0]);
    setCurrentUser(null);

    expect(getCurrentUser()).toBeNull();
  });

  it("treats an unknown stored value as anonymous", () => {
    localStorage.setItem("openarchive.demo-user", "unknown-user");

    expect(getCurrentUser()).toBeNull();
  });
});
