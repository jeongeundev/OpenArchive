import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/AuthProvider";
import { formatExpiry } from "@/lib/tokenExpiry";
import SharesPage from "./page";

const admin = { authenticated: true, username: "admin", is_admin: true };
const token = { id: "t1", name: "外部봇", created_at: "2026-10-01T00:00:00Z", expires_at: "2026-10-12T09:30:00Z", last_used_at: "2026-10-08T09:30:00Z", expired: true };
const shares = [{ id: "s1", name: "협업", owner: "kim", created_at: token.created_at, document_count: 3, folder_count: 2, tokens: [token, { ...token, id: "t2", name: "미사용", expires_at: null, last_used_at: null, expired: false }] }, { id: "s2", name: "빈 공유", owner: "lee", created_at: token.created_at, document_count: 0, folder_count: 1, tokens: [] }];
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function setup(items = shares, auth = admin, failure = false) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/auth/me") return response(auth);
    if (init?.method === "DELETE") return failure ? response({ detail: "토큰을 찾을 수 없습니다." }, 404) : new Response(null, { status: 204 });
    return response(items);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthProvider><SharesPage /></AuthProvider>);
  return fetchMock;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it("공유와 토큰 메타데이터만 표시한다", async () => {
  setup();
  expect(await screen.findByText("협업")).toBeInTheDocument();
  for (const label of ["kim", "lee", "3", "0", "2", "1", "폴더 수", "外部봇", "만료", `만료 ${formatExpiry(token.expires_at)}`, "만료 없음", "사용 기록 없음", "토큰 없음"]) expect(screen.getByText(label)).toBeInTheDocument();
  const date = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(token.last_used_at));
  expect(screen.getByText(`마지막 사용 ${date}`)).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(screen.queryByText(/문서 제목|🔒/)).not.toBeInTheDocument();
});
it("확인 후 해당 토큰을 폐기하고 목록에서 제거한다", async () => {
  const fetchMock = setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(await screen.findByRole("button", { name: "外部봇 폐기" }));
  await waitFor(() => expect(screen.queryByText("外部봇")).not.toBeInTheDocument());
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("협업"));
  expect(fetchMock).toHaveBeenCalledWith("/api/admin/shares/s1/tokens/t1", expect.objectContaining({ method: "DELETE" }));
  expect(screen.getByText("미사용")).toBeInTheDocument();
});
it("취소하면 폐기하지 않는다", async () => {
  const fetchMock = setup();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(await screen.findByRole("button", { name: "外部봇 폐기" }));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(screen.getByText("外部봇")).toBeInTheDocument();
});
it("폐기 실패 문구를 보이고 토큰을 유지한다", async () => {
  setup(shares, admin, true);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(await screen.findByRole("button", { name: "外部봇 폐기" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("토큰을 찾을 수 없습니다.");
  expect(screen.getByText("外部봇")).toBeInTheDocument();
});
it("관리자가 아니면 목록을 요청하지 않는다", async () => {
  const fetchMock = setup(shares, { ...admin, is_admin: false });
  expect(await screen.findByText("관리자 권한이 필요합니다.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("공유가 없으면 빈 상태를 표시한다", async () => {
  setup([]);
  expect(await screen.findByText("외부 공유가 없습니다.")).toBeInTheDocument();
});
