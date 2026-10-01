import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GranteePicker } from "./GranteePicker";

const principals = { users: ["alice", "bob", "carol"], groups: ["인사팀", "재무팀"] };

describe("GranteePicker", () => {
  it("사용자와 그룹을 골라 추가한다", () => {
    const onChange = vi.fn();
    render(<GranteePicker groups={[]} onChange={onChange} principals={principals} users={[]} />);

    fireEvent.change(screen.getByLabelText("사용자 선택"), { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 추가" }));
    expect(onChange).toHaveBeenLastCalledWith({ users: ["bob"], groups: [] });

    fireEvent.change(screen.getByLabelText("그룹 선택"), { target: { value: "인사팀" } });
    fireEvent.click(screen.getByRole("button", { name: "그룹 추가" }));
    expect(onChange).toHaveBeenLastCalledWith({ users: [], groups: ["인사팀"] });
  });

  it("선택된 대상을 사용자·그룹으로 구분해 보이고 제거할 수 있다", () => {
    const onChange = vi.fn();
    render(
      <GranteePicker groups={["인사팀"]} onChange={onChange} principals={principals} users={["bob"]} />,
    );

    expect(within(screen.getByRole("list", { name: "부여된 사용자" })).getByText("bob")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "부여된 그룹" })).getByText("인사팀")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "사용자 bob 제거" }));
    expect(onChange).toHaveBeenLastCalledWith({ users: [], groups: ["인사팀"] });
    fireEvent.click(screen.getByRole("button", { name: "그룹 인사팀 제거" }));
    expect(onChange).toHaveBeenLastCalledWith({ users: ["bob"], groups: [] });
  });

  it("이미 고른 대상은 후보에서 빠진다", () => {
    render(
      <GranteePicker groups={["인사팀"]} onChange={vi.fn()} principals={principals} users={["bob"]} />,
    );

    const userOptions = within(screen.getByLabelText("사용자 선택"))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(userOptions).not.toContain("bob");
    expect(userOptions).toContain("alice");
    const groupOptions = within(screen.getByLabelText("그룹 선택"))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(groupOptions).not.toContain("인사팀");
    expect(groupOptions).toContain("재무팀");
  });

  it("그룹 구성원은 관리자가 바꿀 수 있다고 안내한다", () => {
    render(<GranteePicker groups={[]} onChange={vi.fn()} principals={principals} users={[]} />);
    expect(screen.getByText(/관리자도 보면 안 되는 문서는 사용자에게 직접 부여하세요/)).toBeInTheDocument();
  });
});
