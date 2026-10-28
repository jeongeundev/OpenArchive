// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
    if (url.startsWith("/api/admin/audit/counts")) return response({ total: 123, actions: Object.fromEntries(["document_created", "document_deleted", "folder_access_changed", "owner_changed", "share_changed", "group_changed", "user_changed", "password_changed", "folder_changed", "token_changed", "document_trashed", "document_restored", "original_previewed"].map(action => [action, 3])) });
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

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

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
    // 관리자에게 보이는 것은 대상 문서 제목과 폴더 이름뿐이다 (ADR-055 결정 8, 2026-10-12)
    expect(screen.getByText(/대상 문서의 제목과 폴더 이름만 보이고, 문서 본문은 보이지 않습니다\./)).toBeInTheDocument();
  });

  it("동작을 표기 규칙대로 보여 준다", async () => {
    const items = [
      entry({ action: "document_deleted", document_title: "삭제한 문서" }),
      entry({ action: "document_trashed", document_title: "버린 문서" }),
      entry({ action: "document_restored", document_title: "되살린 문서" }),
      entry({ action: "access_changed", detail: { kind: "visibility", before: "public", after: "private" } }),
      entry({ action: "access_changed", detail: { kind: "grant", change: "added", grantee_type: "user", grantee: "bob" } }),
      entry({ action: "access_changed", detail: { kind: "grant", change: "removed", grantee_type: "group", grantee: "재무팀" } }),
      entry({ action: "group_member_changed", actor: "admin", document_id: null, document_title: null, detail: { change: "added", group: "재무팀", user: "bob" } }),
      entry({ action: "group_member_changed", actor: "admin", document_id: null, document_title: null, detail: { change: "removed", group: "재무팀", user: "bob" } }),
      entry({ action: "original_replaced", detail: { file_version: 2 } }),
      entry({ action: "original_downloaded", detail: { file_version: 1 } }),
      entry({ action: "original_previewed", detail: { file_version: 2 } }),
    ];
    vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("영구 삭제")).toBeInTheDocument();
    expect(within(table).getByText("삭제한 문서")).toBeInTheDocument();
    expect(within(table).getByText("휴지통 이동")).toBeInTheDocument();
    expect(within(table).getByText("버린 문서")).toBeInTheDocument();
    expect(within(table).getByText("복원")).toBeInTheDocument();
    expect(within(table).getByText("되살린 문서")).toBeInTheDocument();
    for (const label of ["휴지통 이동", "복원", "영구 삭제", "원본 미리보기"]) {
      expect(screen.getByRole("button", { name: `${label} 3` })).toBeInTheDocument();
    }
    expect(within(table).getAllByText("열람 범위 변경")).toHaveLength(3);
    expect(within(table).getByText("조직 공개 → 제한")).toBeInTheDocument();
    expect(within(table).getByText("사용자 bob 추가")).toBeInTheDocument();
    expect(within(table).getByText("그룹 재무팀 제거")).toBeInTheDocument();
    expect(within(table).getAllByText("그룹 구성원 변경")).toHaveLength(2);
    expect(within(table).getByText("재무팀에 bob 추가")).toBeInTheDocument();
    expect(within(table).getByText("재무팀에서 bob 제거")).toBeInTheDocument();
    expect(within(table).getByText("원본 교체(2판)")).toBeInTheDocument();
    expect(within(table).getByText("원본 내려받기(1판)")).toBeInTheDocument();
    expect(within(table).getByText("원본 미리보기(2판)")).toBeInTheDocument();
    const memberRow = rows()[6];
    expect(within(memberRow).getByText("admin")).toBeInTheDocument();
    expect(within(memberRow).getByText("—")).toBeInTheDocument();
  });

  it("폴더 범위·부여·상속·이동 기록을 지정 문구와 대상으로 보여 준다", async () => {
    const items = [
      entry({ action: "folder_access_changed", document_id: null, document_title: null, detail: { kind: "visibility", folder_name: "RFP", before: "public", after: "private" } }),
      entry({ action: "folder_access_changed", document_id: null, document_title: null, detail: { kind: "grant", change: "added", grantee_type: "group", grantee: "사업팀", folder_name: "RFP" } }),
      entry({ action: "folder_access_changed", document_id: null, document_title: null, detail: { kind: "grant", change: "removed", grantee_type: "user", grantee: "lee", folder_name: "RFP" } }),
      entry({ action: "access_changed", detail: { kind: "inherit", before: "folder", after: "own" } }),
      entry({ action: "access_changed", detail: { kind: "folder", before: "인사", after: "RFP" } }),
      entry({ action: "access_changed", detail: { kind: "folder", before: null, after: "RFP" } }),
    ];
    vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("폴더 열람 범위 변경")).toHaveLength(3);
    expect(within(table).getAllByText("폴더 「RFP」")).toHaveLength(3);
    for (const description of ["조직 공개 → 제한", "그룹 사업팀 추가", "사용자 lee 제거", "폴더 범위 따름 → 개별 지정", "폴더 「인사」 → 「RFP」", "폴더 없음 → 폴더 「RFP」"]) {
      expect(within(table).getByText(description)).toBeInTheDocument();
    }
    expect(within(rows()[3]).getByText("인사 규정")).toBeInTheDocument();
  });

  it("폴더 열람 범위 변경을 동작 필터로 요청한다", async () => {
    const { fetchMock, auditUrls } = routedFetch(admin, [{ items: [], next_before_id: null }]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "폴더 열람 범위 변경 3" }));
    await waitFor(() => expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.get("action")).toBe("folder_access_changed"));
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
    // 워커는 텍스트 인식만이 아니라 휴지통 자동 영구 삭제도 남긴다 (services/trash.py)
    expect(screen.getByText("시스템(워커)")).toBeInTheDocument();
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
    await waitFor(() => expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.get("actor")).toBe("bob"));

    fireEvent.click(screen.getByRole("button", { name: "영구 삭제 3" }));
    await waitFor(() =>
      expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.get("action")).toBe("document_deleted"),
    );
  });

  it("다음 쪽으로 교체하고 이전 커서로 돌아온다", async () => {
    const first = entry({ document_title: "첫 쪽" });
    const second = entry({ document_title: "둘째 쪽" });
    const { fetchMock, auditUrls } = routedFetch(admin, [
      { items: [first], next_before_id: first.id },
      { items: [second], next_before_id: null },
      { items: [first], next_before_id: first.id },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthProvider><AuditPage /></AuthProvider>);
    await screen.findByText("첫 쪽");
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "다음" }));

    await screen.findByText("둘째 쪽");
    expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.get("before_id")).toBe(String(first.id));
    expect(screen.queryByText("첫 쪽")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "이전" }));
    await waitFor(() => expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.has("before_id")).toBe(false));
    await screen.findByText("첫 쪽");
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
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
    expect(screen.queryAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link")).toHaveTextContent("CSV 내려받기");
  });
});

it("소유자 변경 동작과 문서·폴더 설명을 표시한다", async () => {
  const { fetchMock } = routedFetch(admin, [{ items: [entry({ action: "owner_changed", detail: { kind: "document", before: "kim", after: "lee" } }), entry({ action: "owner_changed", document_title: null, detail: { kind: "folder", folder_name: "RFP", before: "kim", after: "lee" } })], next_before_id: null }]);
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  expect(await screen.findByText("kim → lee")).toBeInTheDocument();
  expect(screen.getByText("폴더 「RFP」 kim → lee")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "소유자 변경 3" })).toBeInTheDocument();
  expect(screen.getAllByText("소유자 변경")).toHaveLength(2);
});


it("공유의 폴더 추가·제거를 폴더 이름과 함께 표시한다", async () => {
  const items = ["folder_added", "folder_removed"].map(change => entry({ action: "share_changed", actor: "kim", document_title: null, detail: { change, share_name: "협업", owner: "kim", folder_name: "RFP" } }));
  vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  const table = await screen.findByRole("table");
  expect(within(table).getByText("「협업」에 폴더 「RFP」 추가")).toBeInTheDocument();
  expect(within(table).getByText("「협업」에서 폴더 「RFP」 제거")).toBeInTheDocument();
});

it("공유·그룹·사용자 변경의 종류와 공유 주인을 표시한다", async () => {
  const changes = ["created", "deleted", "document_added", "document_removed", "token_issued", "token_revoked"];
  const items = changes.map(change => entry({ action: "share_changed", actor: "admin", document_title: null, detail: { change, share_name: "협업", owner: "kim", token_name: "외부봇" } }));
  for (const change of ["created", "deleted"]) {
    items.push(entry({ action: "group_changed", detail: { change, group: "개발팀" } }));
    items.push(entry({ action: "user_changed", detail: { change, user: "lee" } }));
  }
  vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  const table = await screen.findByRole("table");
  for (const label of ["공유 「협업」 생성", "공유 「협업」 삭제", "「협업」에 문서 추가", "「협업」에서 문서 제거", "「협업」 토큰 「외부봇」 발급", "「협업」 토큰 「외부봇」 폐기"]) expect(within(table).getByText(`${label} (소유자 kim)`)).toBeInTheDocument();
  for (const label of ["그룹 개발팀 생성", "그룹 개발팀 삭제", "사용자 lee 생성", "사용자 lee 삭제"]) expect(within(table).getByText(label)).toBeInTheDocument();
  for (const label of ["외부 공유 변경", "그룹 변경", "사용자 변경"]) expect(screen.getByRole("button", { name: `${label} 3` })).toBeInTheDocument();
});
it("CSV 링크가 현재 사용자·동작 필터를 인코딩한다", async () => {
  users.push({ ...users[0], id: "encoded", username: "kim & lee" });
  try {
    vi.stubGlobal("fetch", routedFetch(admin, [{ items: [], next_before_id: null }]).fetchMock);
    render(<AuthProvider><AuditPage /></AuthProvider>);
    const link = await screen.findByRole("link", { name: "CSV 내려받기" });
    expect(new URL(link.getAttribute("href")!, "http://localhost").searchParams.get("since")).toBeTruthy();
    await screen.findByRole("option", { name: "kim & lee" });
    fireEvent.change(screen.getByRole("combobox", { name: "사용자" }), { target: { value: "kim & lee" } });
    fireEvent.click(await screen.findByRole("button", { name: "외부 공유 변경 3" }));
    expect(new URL(link.getAttribute("href")!, "http://localhost").searchParams.get("actor")).toBe("kim & lee");
    expect(new URL(link.getAttribute("href")!, "http://localhost").searchParams.get("action")).toBe("share_changed");
    fireEvent.change(screen.getByPlaceholderText("문서 제목으로 찾기"), { target: { value: "공유 규정" } });
    await waitFor(() => expect(new URL(link.getAttribute("href")!, "http://localhost").searchParams.get("q")).toBe("공유 규정"));
    const csv = new URL(link.getAttribute("href")!, "http://localhost").searchParams;
    expect(csv.get("actor")).toBe("kim & lee");
    expect(csv.get("action")).toBe("share_changed");
    expect(csv.get("since")).toBeTruthy();
    expect(link).toHaveAttribute("download");
  } finally { users.pop(); }
});

it("비밀번호·관리자 권한·폴더·토큰 변경을 표시하고 동작 필터에 둔다", async () => {
  const items = [
    entry({ action: "password_changed", document_id: null, document_title: null, detail: { user: "lee" } }),
    entry({ action: "user_changed", document_id: null, document_title: null, detail: { change: "admin_granted", user: "lee" } }),
    entry({ action: "user_changed", document_id: null, document_title: null, detail: { change: "admin_revoked", user: "lee" } }),
    entry({ action: "user_changed", document_id: null, document_title: null, detail: { change: "created", user: "kim", is_admin: true } }),
    entry({ action: "folder_changed", document_id: null, document_title: null, detail: { change: "created", folder_id: "f-1", folder_name: "RFP", parent_id: null } }),
    entry({ action: "folder_changed", document_id: null, document_title: null, detail: { change: "renamed", folder_id: "f-1", folder_name: "제안", old_name: "RFP", parent_id: null } }),
    entry({ action: "folder_changed", document_id: null, document_title: null, detail: { change: "deleted", folder_id: "f-1", folder_name: "제안", parent_id: null } }),
    entry({ action: "token_changed", actor: "admin", document_id: null, document_title: null, detail: { change: "issued", owner: "lee", token_name: "배치", scope: "read", expires_at: null } }),
    entry({ action: "token_changed", actor: "lee", document_id: null, document_title: null, detail: { change: "revoked", owner: "lee", token_name: "배치", scope: "read", expires_at: null } }),
  ];
  vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  const table = await screen.findByRole("table");
  expect(within(table).getByText("비밀번호 변경")).toBeInTheDocument();
  expect(within(table).getAllByText("폴더 변경")).toHaveLength(3);
  expect(within(table).getAllByText("토큰 변경")).toHaveLength(2);
  for (const label of [
    "사용자 lee", "사용자 lee 관리자 권한 부여", "사용자 lee 관리자 권한 회수", "사용자 kim 생성(관리자)",
    "폴더 「RFP」 생성", "폴더 「RFP」 → 「제안」", "폴더 「제안」 삭제",
    "토큰 「배치」 발급 (소유자 lee)", "토큰 「배치」 폐기",
  ]) expect(within(table).getByText(label)).toBeInTheDocument();
  expect(within(rows()[4]).getByText("폴더 「RFP」")).toBeInTheDocument();
  for (const [name] of [["비밀번호 변경", "password_changed"], ["폴더 변경", "folder_changed"], ["토큰 변경", "token_changed"]]) {
    expect(screen.getByRole("button", { name: `${name} 3` })).toBeInTheDocument();
  }
});

it("소속 부서 변경과 부서 폴더 지정·해제를 표시한다 (D18)", async () => {
  const items = [
    entry({ action: "user_changed", document_id: null, document_title: null, detail: { change: "department_changed", user: "김민준", from: null, to: "IT전략실" } }),
    entry({ action: "user_changed", document_id: null, document_title: null, detail: { change: "department_changed", user: "김민준", from: "IT전략실", to: null } }),
    entry({ action: "folder_changed", document_id: null, document_title: null, detail: { change: "department_set", folder_id: "f-1", folder_name: "IT전략실", department: "IT전략실" } }),
    entry({ action: "folder_changed", document_id: null, document_title: null, detail: { change: "department_cleared", folder_id: "f-1", folder_name: "IT전략실", department: "IT전략실" } }),
  ];
  vi.stubGlobal("fetch", routedFetch(admin, [{ items, next_before_id: null }]).fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  const table = await screen.findByRole("table");
  for (const label of [
    "사용자 「김민준」 소속 부서: 없음 → IT전략실",
    "사용자 「김민준」 소속 부서: IT전략실 → 없음",
    "폴더 「IT전략실」 부서 폴더 지정: IT전략실",
    "폴더 「IT전략실」 부서 폴더 해제",
  ]) expect(within(table).getByText(label)).toBeInTheDocument();
});


it("기본 7일·서버 집계·검색 디바운스·CSV 조건을 적용한다", async () => {
  const { fetchMock, auditUrls } = routedFetch(admin, [{ items: [], next_before_id: null }]);
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  expect(await screen.findByRole("button", { name: "전체 123" })).toBeInTheDocument();
  const first = new URL(auditUrls[0], "http://localhost").searchParams;
  expect(Date.now() - Date.parse(first.get("since")!)).toBeGreaterThanOrEqual(7 * 86400000);
  expect(screen.getByRole("combobox", { name: "기간" })).toHaveValue("7d");
  fireEvent.change(screen.getByPlaceholderText("문서 제목으로 찾기"), { target: { value: "규정" } });
  expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.has("q")).toBe(false);
  await waitFor(() => expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.get("q")).toBe("규정"));
  expect(new URL(screen.getByRole("link", { name: "CSV 내려받기" }).getAttribute("href")!, "http://localhost").searchParams.get("q")).toBe("규정");
  fireEvent.change(screen.getByRole("combobox", { name: "기간" }), { target: { value: "all" } });
  await waitFor(() => expect(new URL(auditUrls.at(-1)!, "http://localhost").searchParams.has("since")).toBe(false));
});

it("주소의 동작을 초기 선택하고 0건이어도 선택 칩을 남긴다", async () => {
  window.history.replaceState({}, "", "/admin/audit?action=text_updated");
  vi.stubGlobal("fetch", routedFetch(admin, [{ items: [], next_before_id: null }]).fetchMock);
  render(<AuthProvider><AuditPage /></AuthProvider>);
  expect(await screen.findByRole("button", { name: "텍스트 수정 0" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(await screen.findByRole("button", { name: "전체 123" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "텍스트 수정 0" })).not.toBeInTheDocument());
});
