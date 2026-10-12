import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Folder, FolderScope } from "@/lib/types";
import OrganizePage from "./page";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const publicScope: FolderScope = { visibility: "public", users: [], groups: [] };
const bizScope: FolderScope = { visibility: "private", users: [], groups: ["사업팀"] };
const insa: Folder = { id: "f-insa", name: "인사", parent_id: null, created_by: "alice", document_count: 3,
  scope: publicScope, inherited: false, can_manage: true, can_change_access: true, can_share: false };
const rfp: Folder = { id: "f-rfp", name: "RFP", parent_id: null, created_by: "kim", document_count: 2,
  scope: bizScope, inherited: false, can_manage: false, can_change_access: false, can_share: false };

const unfiled = {
  count: 3,
  items: [
    { document_id: "d-leave", title: "연차 규정",
      suggestion: { folder_id: "f-insa", path: [{ id: "f-insa", name: "인사" }], votes: 3, total: 4 } },
    { document_id: "d-rfp", title: "제안요청서",
      suggestion: { folder_id: "f-rfp", path: [{ id: "f-rfp", name: "RFP" }], votes: 2, total: 2 } },
    { document_id: "d-lonely", title: "외톨이 메모", suggestion: null },
  ],
};

/** 경로·메서드로 응답을 고르고 이동 요청을 모은다. 문서는 폴더 밖이라 자기 범위(조직 공개)를 따른다. */
function routedFetch(list: unknown = unfiled) {
  const moves: { url: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/documents/unfiled") return response(list);
    if (url === "/api/folders") return response([insa, rfp]);
    if (url.endsWith("/access")) {
      return response({ follows_folder: true, folder: null, folder_scope: null, hidden_folder: false,
        visibility: "public", users: [], groups: [] });
    }
    if (url.endsWith("/folder") && method === "PUT") {
      moves.push({ url, body: JSON.parse(String(init?.body)) });
      return response({});
    }
    return response({ detail: "없음" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return moves;
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  if (row === null) throw new Error(`${title} 행이 없다`);
  return row;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("정리할 문서 화면", () => {
  it("폴더 밖 내 문서를 추천 폴더·근거와 함께 보이고, 추천이 없는 문서는 그렇다고 알린다", async () => {
    routedFetch();
    render(<OrganizePage />);

    await screen.findByText("연차 규정");
    const leave = rowOf("연차 규정");
    expect(within(leave).getByRole("link", { name: "연차 규정" })).toHaveAttribute("href", "/documents/d-leave");
    expect(within(leave).getByText("인사")).toBeInTheDocument();
    expect(within(leave).getByText("폴더에 있는 관련 문서 4개 중 3개")).toBeInTheDocument();
    expect(within(rowOf("외톨이 메모")).getByText("추천할 폴더가 없습니다")).toBeInTheDocument();
    expect(within(rowOf("외톨이 메모")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("추천 폴더로 옮기면 그 행이 목록에서 빠진다", async () => {
    const moves = routedFetch();
    const confirm = vi.spyOn(window, "confirm");
    render(<OrganizePage />);

    await screen.findByText("연차 규정");
    const button = within(rowOf("연차 규정")).getByRole("button", { name: "인사 폴더로 옮기기" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(screen.queryByText("연차 규정")).not.toBeInTheDocument());
    expect(moves).toEqual([{ url: "/api/documents/d-leave/folder", body: { folder_id: "f-insa" } }]);
    expect(confirm).not.toHaveBeenCalled();
  });

  it("열람 범위가 바뀌는 추천은 확인을 받고, 취소하면 옮기지 않는다", async () => {
    const moves = routedFetch();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<OrganizePage />);

    await screen.findByText("제안요청서");
    const button = within(rowOf("제안요청서")).getByRole("button", { name: "RFP 폴더로 옮기기" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(
      "열람 범위가 바뀝니다: 「조직 공개」 → 「제한 · 사업팀」. 옮기시겠습니까?"));
    expect(moves).toEqual([]);
    expect(screen.getByText("제안요청서")).toBeInTheDocument();
  });

  it("목록보다 문서가 많으면 최근 것만 보인다고 알린다", async () => {
    routedFetch({ ...unfiled, count: 120 });
    render(<OrganizePage />);

    expect(await screen.findByText("폴더 밖 문서 120건 중 최근 3건입니다.")).toBeInTheDocument();
  });

  it("폴더 밖 문서가 없으면 비었다고 알린다", async () => {
    routedFetch({ count: 0, items: [] });
    render(<OrganizePage />);

    expect(await screen.findByText("폴더 밖에 있는 내 문서가 없습니다.")).toBeInTheDocument();
  });
});
