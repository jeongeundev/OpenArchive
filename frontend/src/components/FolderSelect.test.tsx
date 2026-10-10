import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FolderSelect } from "./FolderSelect";
it("labels tree options and reports folder ids and null", () => {
  const onChange = vi.fn();
  const scope = {visibility: "public" as const, users: [], groups: []};
  const folders = [{id: "a", name: "인사", parent_id: null, created_by: "kim", document_count: 0, scope, inherited: false, can_manage: true, can_change_access: true, can_share: false}, {id: "b", name: "채용", parent_id: "a", created_by: "kim", document_count: 0, scope, inherited: true, can_manage: true, can_change_access: false, can_share: false}];
  const {rerender} = render(<FolderSelect folders={folders} value={null} onChange={onChange} label="폴더" noneLabel="폴더 없음" id="folder" />);
  const select = screen.getByLabelText("폴더");
  expect(screen.getAllByRole("option").map(o => o.textContent)).toEqual(["폴더 없음", "인사", "인사/채용"]);
  fireEvent.change(select, {target: {value: "b"}});
  expect(onChange).toHaveBeenLastCalledWith("b");
  fireEvent.change(select, {target: {value: ""}});
  expect(onChange).toHaveBeenLastCalledWith(null);
  rerender(<FolderSelect folders={folders} value="a" onChange={onChange} label="폴더" noneLabel="전체 폴더" disabled />);
  expect(screen.getByLabelText("폴더")).toBeDisabled();
  expect(screen.getByLabelText("폴더")).toHaveValue("a");
});
