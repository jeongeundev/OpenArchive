import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccessPanel } from "./AccessPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const principals = { users: ["alice", "bob"], groups: ["인사팀"] };

function stubFetch(
  access: { visibility: "public" | "private"; users: string[]; groups: string[] },
  save: () => Response = () => jsonResponse(access),
) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/principals") return Promise.resolve(jsonResponse(principals));
    if (url.endsWith("/access") && init?.method === "PUT") return Promise.resolve(save());
    if (url.endsWith("/access")) return Promise.resolve(jsonResponse(access));
    return Promise.reject(new Error(`unexpected ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function savedBodies(fetchMock: ReturnType<typeof stubFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => url.endsWith("/access") && init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

async function renderPanel(onSaved = vi.fn()): Promise<void> {
  await act(async () => {
    render(<AccessPanel documentId="document-1" onSaved={onSaved} />);
  });
}

describe("AccessPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("조직 공개 문서에는 대상 선택이 없다", async () => {
    stubFetch({ visibility: "public", users: [], groups: [] });
    await renderPanel();

    expect(await screen.findByRole("radio", { name: "조직 공개" })).toBeChecked();
    expect(screen.queryByLabelText("사용자 선택")).not.toBeInTheDocument();
  });

  it("제한 문서는 현재 부여 대상을 보여 준다", async () => {
    stubFetch({ visibility: "private", users: ["bob"], groups: ["인사팀"] });
    await renderPanel();

    expect(await screen.findByRole("radio", { name: "제한" })).toBeChecked();
    expect(await screen.findByRole("button", { name: "사용자 bob 제거" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "그룹 인사팀 제거" })).toBeInTheDocument();
  });

  it("제한으로 바꾸고 대상을 골라 저장한다", async () => {
    const onSaved = vi.fn();
    const fetchMock = stubFetch({ visibility: "public", users: [], groups: [] }, () =>
      jsonResponse({ visibility: "private", users: ["bob"], groups: [] }),
    );
    await renderPanel(onSaved);

    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    fireEvent.change(await screen.findByLabelText("사용자 선택"), { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(savedBodies(fetchMock)).toEqual([{ visibility: "private", users: ["bob"], groups: [] }]),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  });

  it("조직 공개로 저장하면 부여 대상을 비워 보내고 그 사실을 안내한다", async () => {
    const fetchMock = stubFetch({ visibility: "private", users: ["bob"], groups: ["인사팀"] }, () =>
      jsonResponse({ visibility: "public", users: [], groups: [] }),
    );
    await renderPanel();

    fireEvent.click(await screen.findByRole("radio", { name: "조직 공개" }));
    expect(screen.getByText(/부여 대상도 함께 지워집니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(savedBodies(fetchMock)).toEqual([{ visibility: "public", users: [], groups: [] }]),
    );
  });

  it.each([
    [400, "알 수 없는 대상입니다: zed"],
    [403, "로그인 세션이 필요합니다."],
  ])("저장이 %s로 실패하면 detail을 보이고 선택을 유지한다", async (status, detail) => {
    stubFetch({ visibility: "private", users: [], groups: [] }, () =>
      jsonResponse({ detail }, status),
    );
    await renderPanel();

    fireEvent.change(await screen.findByLabelText("사용자 선택"), { target: { value: "bob" } });
    fireEvent.click(screen.getByRole("button", { name: "사용자 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "사용자 bob 제거" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "제한" })).toBeChecked();
  });

  it("화면을 떠나면 조회를 취소한다", () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<AccessPanel documentId="document-1" onSaved={vi.fn()} />);
    unmount();

    expect(fetchMock).toHaveBeenCalled();
    for (const [, init] of fetchMock.mock.calls) expect(init?.signal?.aborted).toBe(true);
  });
});
