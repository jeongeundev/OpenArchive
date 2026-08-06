import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TagEditor } from "./TagEditor";

function renderEditor(
  overrides: Partial<React.ComponentProps<typeof TagEditor>> = {},
): React.ComponentProps<typeof TagEditor> {
  const props: React.ComponentProps<typeof TagEditor> = {
    disabled: false,
    error: null,
    onChange: vi.fn(),
    onSave: vi.fn(),
    saving: false,
    tags: ["OpenSQL"],
    ...overrides,
  };
  render(<TagEditor {...props} />);
  return props;
}

describe("TagEditor", () => {
  it("태그를 추가하면 전체 목록으로 변경을 알린다", () => {
    const { onChange } = renderEditor();

    fireEvent.change(screen.getByRole("textbox", { name: "태그" }), {
      target: { value: "pgvector" },
    });
    fireEvent.click(screen.getByRole("button", { name: "태그 추가" }));

    expect(onChange).toHaveBeenCalledWith(["OpenSQL", "pgvector"]);
  });

  it("태그를 삭제하면 빠진 전체 목록으로 변경을 알린다", () => {
    const { onChange } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "OpenSQL 태그 삭제" }));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("이미 있는 태그는 다시 더하지 않는다", () => {
    const { onChange } = renderEditor();

    fireEvent.change(screen.getByRole("textbox", { name: "태그" }), {
      target: { value: "OpenSQL" },
    });
    fireEvent.click(screen.getByRole("button", { name: "태그 추가" }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("저장 버튼은 저장을 요청한다", () => {
    const { onSave } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "태그 저장" }));

    expect(onSave).toHaveBeenCalledOnce();
  });

  it("저장 실패 문구를 표시하면서 입력한 내용을 지우지 않는다", () => {
    const { rerender } = render(
      <TagEditor
        disabled={false}
        error={null}
        onChange={vi.fn()}
        onSave={vi.fn()}
        saving={false}
        tags={["OpenSQL"]}
      />,
    );

    const input = screen.getByRole("textbox", { name: "태그" });
    fireEvent.change(input, { target: { value: "보존할 태그" } });
    rerender(
      <TagEditor
        disabled={false}
        error="태그를 수정할 권한이 없습니다."
        onChange={vi.fn()}
        onSave={vi.fn()}
        saving={false}
        tags={["OpenSQL"]}
      />,
    );

    expect(screen.getByText("태그를 수정할 권한이 없습니다.")).toBeInTheDocument();
    expect(input).toHaveValue("보존할 태그");
  });

  it("비활성 상태에서는 입력과 모든 버튼을 막는다", () => {
    renderEditor({ disabled: true });

    expect(screen.getByRole("textbox", { name: "태그" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "태그 추가" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "태그 저장" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "OpenSQL 태그 삭제" })).toBeDisabled();
  });

  it("저장 중에는 컨트롤을 막고 진행 상태를 보인다", () => {
    renderEditor({ saving: true });

    expect(screen.getByRole("button", { name: "저장 중…" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "태그" })).toBeDisabled();
  });
});
