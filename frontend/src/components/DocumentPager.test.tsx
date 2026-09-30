import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocumentPager } from "./DocumentPager";

describe("DocumentPager", () => {
  it("현재 구간과 전체 수를 보이고 끝에서는 그쪽 버튼을 막는다", () => {
    render(<DocumentPager page={0} pageSize={50} total={120} onChange={vi.fn()} />);

    expect(screen.getByText("120건 중 1–50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음" })).toBeEnabled();
  });

  it("마지막 페이지는 남은 수까지만 센다", () => {
    render(<DocumentPager page={2} pageSize={50} total={120} onChange={vi.fn()} />);

    expect(screen.getByText("120건 중 101–120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("버튼이 이웃 페이지로 옮긴다", () => {
    const onChange = vi.fn();
    render(<DocumentPager page={1} pageSize={50} total={120} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "이전" }));
    fireEvent.click(screen.getByRole("button", { name: "다음" }));

    expect(onChange.mock.calls).toEqual([[0], [2]]);
  });

  it("한 페이지에 다 들어가면 아무것도 그리지 않는다", () => {
    const { container } = render(
      <DocumentPager page={0} pageSize={50} total={50} onChange={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("문서가 줄어 현재 페이지가 비면 마지막 페이지로 옮긴다", () => {
    const onChange = vi.fn();
    render(<DocumentPager page={3} pageSize={50} total={120} onChange={onChange} />);

    expect(onChange).toHaveBeenCalledWith(2);
  });
});
