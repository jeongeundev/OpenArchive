import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OwnerTransfer } from "./OwnerTransfer";
const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); push.mockClear(); });
function setup(visible: boolean, status = 200) {
  const fetchMock = vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(url === "/api/principals" ? { users: ["kim", "lee"], groups: [] } : status === 200 ? { owner_id: "lee", still_visible: visible } : { detail: "이전할 수 없습니다." }), { status: url === "/api/principals" ? 200 : status })));
  vi.stubGlobal("fetch", fetchMock);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onTransferred = vi.fn();
  render(<OwnerTransfer documentId="doc" owner="kim" onTransferred={onTransferred} />);
  return { fetchMock, confirm, onTransferred };
}
describe("OwnerTransfer", () => {
  it.each([true, false])("transfers and follows visibility %s", async visible => {
    const { fetchMock, confirm, onTransferred } = setup(visible);
    expect(screen.getByRole("button", { name: "소유자 이전" })).toBeDisabled();
    await screen.findByRole("option", { name: "lee" });
    expect(screen.queryByRole("option", { name: "kim" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("이전받을 사용자"), { target: { value: "lee" } });
    fireEvent.click(screen.getByRole("button", { name: "소유자 이전" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("이전하면 열람 범위대로만 봅니다"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/documents/doc/owner", expect.objectContaining({ method: "PUT", body: JSON.stringify({ owner: "lee" }) })));
    await waitFor(() => visible ? expect(onTransferred).toHaveBeenCalled() : expect(push).toHaveBeenCalledWith("/"));
  });
  it("preserves selection on server error", async () => {
    const { onTransferred } = setup(true, 400);
    await screen.findByRole("option", { name: "lee" });
    fireEvent.change(screen.getByLabelText("이전받을 사용자"), { target: { value: "lee" } });
    fireEvent.click(screen.getByRole("button", { name: "소유자 이전" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("이전할 수 없습니다.");
    expect(screen.getByLabelText("이전받을 사용자")).toHaveValue("lee");
    expect(onTransferred).not.toHaveBeenCalled();
  });
});
