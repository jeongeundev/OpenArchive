import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/components/AuthProvider";
import type { AuditEntry } from "@/lib/types";
import AuditPage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const admin = { authenticated: true, username: "admin", is_admin: true };
const member = { authenticated: true, username: "alice", is_admin: false };
const users = [
  { id: "u-1", username: "alice", is_admin: false, created_at: "2026-10-01T00:00:00Z" },
  { id: "u-2", username: "bob", is_admin: false, created_at: "2026-10-01T00:00:00Z" },
];

let nextId = 100;
function entry(overrides: Partial<AuditEntry>): AuditEntry {
  nextId -= 1;
  return {
    id: nextId,
    occurred_at: "2026-10-05T09:30:00Z",
    action: "document_created",
    actor: "alice",
    actor_via: "session",
    db_role: "openarchive",
    document_id: "d-1",
    document_title: "인사 규정",
    detail: {},
    ...overrides,
  };
}

/** 경로로 응답을 고르고, 감사 로그 요청 URL을 모은다. */
function routedFetch(auth: unknown, pages: unknown[]) {
  const auditUrls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/auth/me") return response(auth);
    if (url === "/api/admin/users") return response(users);
    if (url.startsWith("/api/admin/audit")) {
      const body = pages[Math.min(auditUrls.length, pages.length - 1)];
      auditUrls.push(url);
      return response(body);
    }
    return response({ detail: "없음" }, 404);
  });
  return { fetchMock, auditUrls };
}

function rows(): HTMLElement[] {
  return screen.getAllByRole("row").slice(1);
}

describe("감사 로그 화면", () => {
  it("기록을 받은 순서(최신순)대로 시각·사용자·동작·대상 문서 제목과 함께 보여 준다", async () => {
    const items = [
      entry({ action: "text_updated", actor: "bob", detail: { version: 2 }, document_title: "보안 지침" }),
      entry({ action: "document_created", actor: "alice", document_title: "인사 규정" }),
    ];
    const { fetchMock } = routedFetch(admin, [{ items, next_before_id: null }]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    await screen.findByText("텍스트 수정(v2)");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["시각", "사용자", "동작", "대상 문서"]);
    const [first, second] = rows();
    expect(within(first).getByText("bob")).toBeInTheDocument();
    expect(within(first).getByText("보안 지침")).toBeInTheDocument();
    expect(within(second).getByText("문서 생성")).toBeInTheDocument();
    expect(within(second).getByText("alice")).toBeInTheDocument();
    expect(within(second).getByText("인사 규정")).toBeInTheDocument();
    expect(first.querySelector("time")).not.toBeNull();
  });

  it("동작을 표기 규칙대로 보여 준다", async () => {
    const items = [
      entry({ action: "document_deleted", document_title: "삭제한 문서" }),
      entry({ action: "access_changed", detail: { kind: "visibility", before: "public", after: "private" } }),
      entry({ action: "access_changed", detail: { kind: "grant", change: "added", grantee_type: "user", grantee: "bob" } }),
      entry({ action: "access_changed", detail: { kind: "grant", change: "removed", grantee_type: "group", grantee: "재무팀" } }),
      entry({ action: "group_member_changed", actor: "admin", document_id: null, document_title: null, detail: { change: "added", group: "재무팀", user: "bob" } }),
      entry({ action: "group_member_changed", actor: "admin", document_id: null, document_title: null, detail: { change: "removed", group: "재무팀", user: "bob" } }),
      entry({ action: "original_replaced", detail: { file_version: 2 } }),
      entry({ action: "original_downloaded", detail: { file_version: 1 } }),
    ];
    vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("문서 삭제")).toBeInTheDocument();
    expect(within(table).getByText("삭제한 문서")).toBeInTheDocument();
    expect(within(table).getAllByText("열람 범위 변경")).toHaveLength(3);
    expect(within(table).getByText("조직 공개 → 제한")).toBeInTheDocument();
    expect(within(table).getByText("사용자 bob 추가")).toBeInTheDocument();
    expect(within(table).getByText("그룹 재무팀 제거")).toBeInTheDocument();
    expect(within(table).getAllByText("그룹 구성원 변경")).toHaveLength(2);
    expect(within(table).getByText("재무팀에 bob 추가")).toBeInTheDocument();
    expect(within(table).getByText("재무팀에서 bob 제거")).toBeInTheDocument();
    expect(within(table).getByText("원본 교체(판 2)")).toBeInTheDocument();
    expect(within(table).getByText("원본 내려받기(판 1)")).toBeInTheDocument();
    const memberRow = rows()[4];
    expect(within(memberRow).getByText("admin")).toBeInTheDocument();
    expect(within(memberRow).getByText("—")).toBeInTheDocument();
  });

  it("행위자 이름이 없으면 경로로 사용자를 표기한다", async () => {
    const items = [
      entry({ action: "original_downloaded", actor: null, actor_via: "share", detail: { file_version: 1, share_name: "B사 협업" } }),
      entry({ action: "text_updated", actor: null, actor_via: "worker", detail: { version: 3 } }),
      entry({ action: "text_updated", actor: null, actor_via: "cli", detail: { version: 4 } }),
      entry({ action: "document_created", actor: null, actor_via: null, db_role: "openarchive" }),
    ];
    vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    expect(await screen.findByText("공유: B사 협업")).toBeInTheDocument();
    expect(screen.getByText("시스템(텍스트 인식)")).toBeInTheDocument();
    expect(screen.getByText("운영자 CLI")).toBeInTheDocument();
    expect(screen.getByText("직접 접속(openarchive)")).toBeInTheDocument();
  });

  it("사용자·동작으로 걸러 다시 요청한다", async () => {
    const { fetchMock, auditUrls } = routedFetch(admin, [
      { items: [entry({})], next_before_id: null },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);
    await screen.findByRole("table");
    await screen.findByRole("option", { name: "bob" });

    fireEvent.change(screen.getByRole("combobox", { name: "사용자" }), { target: { value: "bob" } });
    await waitFor(() => expect(auditUrls.at(-1)).toBe("/api/admin/audit?actor=bob&limit=50"));

    fireEvent.change(screen.getByRole("combobox", { name: "동작" }), { target: { value: "document_deleted" } });
    await waitFor(() =>
      expect(auditUrls.at(-1)).toBe("/api/admin/audit?actor=bob&action=document_deleted&limit=50"),
    );
  });

  it("다음 쪽이 있으면 더 보기로 이어 붙이고, 없으면 버튼이 없다", async () => {
    const first = entry({ document_title: "첫 쪽" });
    const second = entry({ document_title: "둘째 쪽" });
    const { fetchMock, auditUrls } = routedFetch(admin, [
      { items: [first], next_before_id: first.id },
      { items: [second], next_before_id: null },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);
    await screen.findByText("첫 쪽");
    fireEvent.click(screen.getByRole("button", { name: "더 보기" }));

    await screen.findByText("둘째 쪽");
    expect(auditUrls.at(-1)).toBe(`/api/admin/audit?limit=50&before_id=${first.id}`);
    expect(screen.getByText("첫 쪽")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument();
  });

  it("일반 사용자에게는 관리자 권한이 필요하다고 알리고 기록을 요청하지 않는다", async () => {
    const { fetchMock, auditUrls } = routedFetch(member, [{ items: [], next_before_id: null }]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    expect(await screen.findByText("관리자 권한이 필요합니다.")).toBeInTheDocument();
    expect(auditUrls).toHaveLength(0);
  });

  it("대상 문서 제목을 문서 링크로 만들지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      routedFetch(admin, [{ items: [entry({ document_title: "보안 지침" })], next_before_id: null }]).fetchMock,
    );

    render(<AuthProvider><AuditPage /></AuthProvider>);

    const title = await screen.findByText("보안 지침");
    expect(title.closest("a")).toBeNull();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
});
