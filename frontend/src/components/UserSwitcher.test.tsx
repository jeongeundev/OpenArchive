import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEMO_USERS } from "@/lib/user";

import { UserSwitcher } from "./UserSwitcher";

const { getCurrentUser, setCurrentUser } = vi.hoisted(() => ({
  getCurrentUser: vi.fn<() => string | null>(() => null),
  setCurrentUser: vi.fn(),
}));

vi.mock("@/lib/user", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/user")>();
  return {
    ...original,
    getCurrentUser,
    setCurrentUser,
  };
});

describe("UserSwitcher", () => {
  beforeEach(() => {
    getCurrentUser.mockReturnValue(null);
    setCurrentUser.mockClear();
    vi.unstubAllGlobals();
  });

  it("익명과 모든 데모 사용자를 선택지로 표시한다", () => {
    render(<UserSwitcher />);

    expect(screen.getByRole("option", { name: "익명(공개 문서만)" })).toBeInTheDocument();
    for (const user of DEMO_USERS) {
      expect(screen.getByRole("option", { name: user })).toBeInTheDocument();
    }
  });

  it("마운트 후 저장된 데모 사용자를 반영한다", async () => {
    getCurrentUser.mockReturnValue(DEMO_USERS[1]);

    render(<UserSwitcher />);

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue(DEMO_USERS[1]));
  });

  it("데모 사용자 선택을 저장한다", () => {
    render(<UserSwitcher />);
    const reload = vi.fn();
    vi.stubGlobal("window", { location: { reload } });

    fireEvent.change(screen.getByRole("combobox", { name: "데모 사용자" }), {
      target: { value: DEMO_USERS[0] },
    });

    expect(setCurrentUser).toHaveBeenCalledWith(DEMO_USERS[0]);
    expect(reload).toHaveBeenCalledOnce();
  });

  it("익명 선택을 null로 저장한다", () => {
    render(<UserSwitcher />);
    const select = screen.getByRole("combobox", { name: "데모 사용자" });
    vi.stubGlobal("window", { location: { reload: vi.fn() } });
    fireEvent.change(select, { target: { value: DEMO_USERS[0] } });
    setCurrentUser.mockClear();

    fireEvent.change(select, { target: { value: "" } });

    expect(setCurrentUser).toHaveBeenCalledWith(null);
  });
});
