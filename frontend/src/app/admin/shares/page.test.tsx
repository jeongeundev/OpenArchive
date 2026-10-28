// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/AuthProvider";
import { formatExpiry } from "@/lib/tokenExpiry";
import SharesPage from "./page";
import { summarizeShares } from "@/lib/tokenExpiry";

const admin = { authenticated: true, username: "admin", is_admin: true };
const token = { id: "t1", name: "外部봇", created_at: "2026-10-01T00:00:00Z", expires_at: "2026-10-12T09:30:00Z", last_used_at: "2026-10-08T09:30:00Z", expired: true };
const shares = [{ id: "s1", name: "협업", owner: "kim", created_at: token.created_at, document_count: 3, folder_count: 2, tokens: [token, { ...token, id: "t2", name: "미사용", expires_at: null, last_used_at: null, expired: false }] }, { id: "s2", name: "빈 공유", owner: "lee", created_at: token.created_at, document_count: 0, folder_count: 1, tokens: [] }];
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function setup(items = shares, auth = admin, failure = false) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/auth/me") return response(auth);
    if (String(input) === "/api/admin/shares/revoked") return response([]);
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
  for (const label of ["kim", "lee", "3", "· 폴더 2", "· 폴더 1", "토큰 없음", "만료 없음", "사용 기록 없음"]) expect(screen.getByText(label)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "만료 1" }));
  for (const label of ["外部봇", "만료", `만료 ${formatExpiry(token.expires_at)}`]) expect(screen.getByText(label)).toBeInTheDocument();
  // 만료 토큰만 남은 공유도 문서 수가 보여야 한다 (TC 284)
  expect(screen.getByRole("columnheader", { name: "문서 수" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "3" })).toBeInTheDocument();
  const date = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(token.last_used_at));
  expect(screen.getByText(`마지막 사용 ${date}`)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /문서 제목/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/문서 제목|🔒/)).not.toBeInTheDocument();
});
it("확인 후 해당 토큰을 폐기하고 목록에서 제거한다", async () => {
  const fetchMock = setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(await screen.findByRole("tab", { name: "만료 1" }));
  fireEvent.click(screen.getByRole("button", { name: "外部봇 폐기" }));
  await waitFor(() => expect(screen.queryByText("外部봇")).not.toBeInTheDocument());
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("협업"));
  expect(fetchMock).toHaveBeenCalledWith("/api/admin/shares/s1/tokens/t1", expect.objectContaining({ method: "DELETE" }));
  fireEvent.click(screen.getByRole("tab", { name: "활성 2" }));
  expect(screen.getByText("미사용")).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/admin/shares/revoked")).toHaveLength(2);
});
it("취소하면 폐기하지 않는다", async () => {
  const fetchMock = setup();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(await screen.findByRole("tab", { name: "만료 1" }));
  fireEvent.click(screen.getByRole("button", { name: "外部봇 폐기" }));
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(screen.getByText("外部봇")).toBeInTheDocument();
});
it("폐기 실패 문구를 보이고 토큰을 유지한다", async () => {
  setup(shares, admin, true);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(await screen.findByRole("tab", { name: "만료 1" }));
  fireEvent.click(screen.getByRole("button", { name: "外部봇 폐기" }));
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
  expect(await screen.findByText("외부 공유가 없습니다")).toBeInTheDocument();
});

it("빈 탭마다 안내와 감사 로그 링크를 보인다", async () => {
  setup([]);
  expect(await screen.findByRole("link", { name: "감사 로그에서 공유 기록 보기" })).toHaveAttribute("href", "/admin/audit?action=share_changed");
  fireEvent.click(screen.getByRole("tab", { name: "만료 0" }));
  expect(screen.getByText("만료된 토큰이 없습니다")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "폐기 0" }));
  expect(screen.getByText("폐기된 토큰이 없습니다")).toBeInTheDocument();
});
it("7일 경계와 서버 만료 판정으로 요약한다", () => {
  const now = new Date("2026-10-28T00:00:00Z");
  const items = [{ ...shares[0], tokens: [
    { ...token, expired: false, expires_at: "2026-11-04T00:00:00Z", last_used_at: "2026-10-21T00:00:00Z" },
    { ...token, expired: false, expires_at: "2026-11-04T00:00:00.001Z", last_used_at: "2026-10-20T23:59:59Z" },
    { ...token, expired: true, last_used_at: now.toISOString() },
  ] }, shares[1]];
  expect(summarizeShares(items, now)).toEqual({ active: 2, expiring: 1, recent: 2 });
});
it("폐기 탭은 감사 기록의 공유·토큰·사람을 표시한다", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) => response(String(input) === "/api/auth/me" ? admin : String(input).endsWith("/revoked") ? [{ id: 9, share_name: "지난 공유", token_name: "폐기 봇", revoked_at: token.created_at, revoked_by: "관리자kim", actor_via: "session" }] : [])));
  render(<AuthProvider><SharesPage /></AuthProvider>);
  fireEvent.click(await screen.findByRole("tab", { name: "폐기 1" }));
  for (const label of ["지난 공유", "폐기 봇", "관리자kim"]) expect(screen.getByText(label)).toBeInTheDocument();
});
it("활성 탭 숫자는 탭 안의 공유 행 수다", async () => {
  setup([shares[1]]);
  expect(await screen.findByRole("tab", { name: "활성 1" })).toBeInTheDocument();
  expect(screen.getByText("빈 공유")).toBeInTheDocument();
});
it("폐기 기록이 상한만큼 오면 200+로 적는다", async () => {
  const history = Array.from({ length: 200 }, (_, index) => ({ id: index, share_name: `공유${index}`, token_name: `봇${index}`, revoked_at: token.created_at, revoked_by: "admin", actor_via: "session" }));
  vi.stubGlobal("fetch", vi.fn(async (input) => response(String(input) === "/api/auth/me" ? admin : String(input).endsWith("/revoked") ? history : [])));
  render(<AuthProvider><SharesPage /></AuthProvider>);
  expect(await screen.findByRole("tab", { name: "폐기 200+" })).toBeInTheDocument();
  expect(screen.getByText("200+")).toBeInTheDocument();
});
it("폐기는 성공하고 폐기 목록 재조회만 실패하면 폐기 실패라고 하지 않는다", async () => {
  let revokedCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === "/api/auth/me") return response(admin);
    if (String(input) === "/api/admin/shares/revoked") { revokedCalls += 1; return revokedCalls === 1 ? response([]) : response({ detail: "오류" }, 500); }
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return response(shares);
  }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AuthProvider><SharesPage /></AuthProvider>);
  fireEvent.click(await screen.findByRole("tab", { name: "만료 1" }));
  fireEvent.click(screen.getByRole("button", { name: "外部봇 폐기" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("토큰은 폐기했지만 폐기 목록을 새로 불러오지 못했습니다.");
  expect(screen.queryByText("外部봇")).not.toBeInTheDocument();
});
