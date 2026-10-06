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

type ShareStub = { id: string; name: string; documents: { id: string; title: string }[] };

function shareSummary(share: ShareStub) {
  return { ...share, created_at: "2026-10-02T00:00:00Z", tokens: [] };
}

function stubFetch(
  access: { visibility: "public" | "private"; users: string[]; groups: string[]; [key: string]: unknown },
  save: () => Response = () => jsonResponse(access),
  shares: () => Response = () => jsonResponse([]),
  shareWrite: () => Response = () => new Response(null, { status: 204 }),
) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/principals") return Promise.resolve(jsonResponse(principals));
    if (url === "/api/shares") return Promise.resolve(shares());
    if (url.startsWith("/api/shares/") && url.includes("/documents/"))
      return Promise.resolve(shareWrite());
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

    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.getByRole("button", { name: "사용자 bob 제거" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "제한" })).toBeChecked();
  });

  const inFolder = {
    folder: { id: "folder-rfp", name: "RFP", path: [{ id: "folder-rfp", name: "RFP" }] },
    folder_scope: { visibility: "private" as const, users: [], groups: ["사업팀"] },
  };

  function stubFolderDocument(followsFolder: boolean, save: () => Response) {
    const access = { visibility: "private" as const, users: [], groups: [], follows_folder: followsFolder, ...inFolder };
    return stubFetch(access, save);
  }

  it("폴더 안 문서는 「폴더 범위 따름」이면 그 라디오가 골라지고 공개범위 칸이 없다", async () => {
    stubFolderDocument(true, () => jsonResponse({}));
    await renderPanel();

    expect(await screen.findByRole("radio", { name: "폴더 범위 따름(제한 · 사업팀)" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "개별 지정" })).not.toBeChecked();
    expect(screen.queryByRole("radio", { name: "조직 공개" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "제한" })).not.toBeInTheDocument();
    expect(screen.getByText("폴더를 만든 사람이 범위를 바꾸면 이 문서에도 적용됩니다.")).toBeInTheDocument();
  });

  it("「개별 지정」 + 「제한」(대상 없음)으로 저장하면 follows_folder false로 보낸다", async () => {
    const fetchMock = stubFolderDocument(true, () =>
      jsonResponse({ visibility: "private", users: [], groups: [], follows_folder: false, ...inFolder }),
    );
    await renderPanel();

    fireEvent.click(await screen.findByRole("radio", { name: "개별 지정" }));
    expect(screen.getByText("폴더 범위와 상관없이 이 문서만의 열람 범위를 씁니다.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "제한" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(savedBodies(fetchMock)).toEqual([
        { follows_folder: false, visibility: "private", users: [], groups: [] },
      ]),
    );
    expect(await screen.findByText("열람 범위를 저장했습니다.")).toBeInTheDocument();
  });

  it("개별 지정한 문서를 「폴더 범위 따름」으로 되돌리면 follows_folder만 보낸다", async () => {
    const fetchMock = stubFolderDocument(false, () =>
      jsonResponse({ visibility: "private", users: [], groups: [], follows_folder: true, ...inFolder }),
    );
    await renderPanel();

    expect(await screen.findByRole("radio", { name: "개별 지정" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "제한" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "폴더 범위 따름(제한 · 사업팀)" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() => expect(savedBodies(fetchMock)).toEqual([{ follows_folder: true }]));
    expect(await screen.findByText("열람 범위를 저장했습니다.")).toBeInTheDocument();
  });

  it("폴더 밖 문서에는 전환 라디오가 없고 follows_folder를 보내지 않는다", async () => {
    const fetchMock = stubFetch(
      { visibility: "public", users: [], groups: [], follows_folder: true, folder: null, folder_scope: null },
      () => jsonResponse({ visibility: "private", users: [], groups: ["개발팀"] }),
    );
    await renderPanel();

    fireEvent.click(await screen.findByRole("radio", { name: "제한" }));
    expect(screen.queryByRole("radio", { name: "개별 지정" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(savedBodies(fetchMock)).toEqual([{ visibility: "private", users: [], groups: [] }]),
    );
    expect(await screen.findByText("열람 범위를 저장했습니다.")).toBeInTheDocument();
  });

  const inHiddenFolder = { folder: null, folder_scope: null, hidden_folder: true };

  it("볼 수 없는 폴더 안 문서는 폴더 이름 없이 「폴더 범위 따름」으로 보인다", async () => {
    stubFetch({ visibility: "private", users: [], groups: [], follows_folder: true, ...inHiddenFolder });
    await renderPanel();

    expect(await screen.findByRole("radio", { name: "폴더 범위 따름(볼 수 없는 폴더)" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "조직 공개" })).not.toBeInTheDocument();
    expect(screen.queryByText(/RFP/)).not.toBeInTheDocument();
  });

  it("볼 수 없는 폴더 안 문서를 「개별 지정」으로 저장하면 follows_folder false로 보낸다", async () => {
    const fetchMock = stubFetch(
      { visibility: "private", users: [], groups: [], follows_folder: true, ...inHiddenFolder },
      () => jsonResponse({ visibility: "public", users: [], groups: [], follows_folder: false, ...inHiddenFolder }),
    );
    await renderPanel();

    fireEvent.click(await screen.findByRole("radio", { name: "개별 지정" }));
    fireEvent.click(screen.getByRole("radio", { name: "조직 공개" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    await waitFor(() =>
      expect(savedBodies(fetchMock)).toEqual([
        { follows_folder: false, visibility: "public", users: [], groups: [] },
      ]),
    );
  });

  it("「폴더 범위 따름」 저장이 400이면 서버 문구를 보인다", async () => {
    stubFolderDocument(false, () => jsonResponse({ detail: "잘못된 요청입니다." }, 400));
    await renderPanel();

    fireEvent.click(await screen.findByRole("radio", { name: "폴더 범위 따름(제한 · 사업팀)" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("잘못된 요청입니다.");
  });

  const partners: ShareStub = {
    id: "share-1",
    name: "협력사 B",
    documents: [{ id: "document-1", title: "제품 문서" }],
  };
  const auditors: ShareStub = { id: "share-2", name: "감사인", documents: [] };

  function shareWrites(fetchMock: ReturnType<typeof stubFetch>): [string, string | undefined][] {
    return fetchMock.mock.calls
      .filter(([url]) => url.startsWith("/api/shares/"))
      .map(([url, init]) => [url, init?.method]);
  }

  it.each(["public", "private"] as const)(
    "%s 문서에서도 내 공유를 체크박스로 보이고 포함된 공유는 체크되어 있다",
    async (visibility) => {
      stubFetch({ visibility, users: [], groups: [] }, undefined, () =>
        jsonResponse([shareSummary(partners), shareSummary(auditors)]),
      );
      await renderPanel();

      expect(await screen.findByRole("checkbox", { name: "협력사 B" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "감사인" })).not.toBeChecked();
    },
  );

  it("체크하면 공유에 넣고 해제하면 빼며, 열람 범위 저장과 따로 즉시 반영한다", async () => {
    const fetchMock = stubFetch({ visibility: "private", users: [], groups: [] }, undefined, () =>
      jsonResponse([shareSummary(partners), shareSummary(auditors)]),
    );
    await renderPanel();

    fireEvent.click(await screen.findByRole("checkbox", { name: "감사인" }));
    expect(await screen.findByText("「감사인」 공유에 넣었습니다.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "감사인" })).toBeChecked();

    fireEvent.click(screen.getByRole("checkbox", { name: "협력사 B" }));
    expect(await screen.findByText("「협력사 B」 공유에서 뺐습니다.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "협력사 B" })).not.toBeChecked();

    expect(shareWrites(fetchMock)).toEqual([
      ["/api/shares/share-2/documents/document-1", "PUT"],
      ["/api/shares/share-1/documents/document-1", "DELETE"],
    ]);
    expect(savedBodies(fetchMock)).toEqual([]);
  });

  it("공유 반영이 실패하면 체크 상태를 되돌리고 오류를 보인다", async () => {
    stubFetch(
      { visibility: "private", users: [], groups: [] },
      undefined,
      () => jsonResponse([shareSummary(partners)]),
      () => jsonResponse({ detail: "공유를 찾을 수 없습니다." }, 404),
    );
    await renderPanel();

    const box = await screen.findByRole("checkbox", { name: "협력사 B" });
    fireEvent.click(box);

    expect(await screen.findByText("공유를 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "협력사 B" })).toBeChecked();
  });

  it("공유가 없으면 설정 화면에서 만들라고 안내한다", async () => {
    stubFetch({ visibility: "private", users: [], groups: [] });
    await renderPanel();

    const link = await screen.findByRole("link", { name: "설정 화면" });
    expect(link).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("조직 공개 문서가 공유에 포함되어 있으면 외부에도 열려 있음을 안내한다", async () => {
    stubFetch({ visibility: "public", users: [], groups: [] }, undefined, () =>
      jsonResponse([shareSummary(partners)]),
    );
    await renderPanel();

    expect(await screen.findByText(/조직 공개와 별개로 외부 공유에 열려 있습니다/)).toBeInTheDocument();
  });

  it("공유에 포함되지 않았으면 외부 공개 안내를 보이지 않는다", async () => {
    stubFetch({ visibility: "public", users: [], groups: [] }, undefined, () =>
      jsonResponse([shareSummary(auditors)]),
    );
    await renderPanel();

    await screen.findByRole("checkbox", { name: "감사인" });
    expect(screen.queryByText(/외부 공유에 열려 있습니다/)).not.toBeInTheDocument();
  });

  it("열람 범위를 저장해도 공유 체크는 그대로다", async () => {
    const fetchMock = stubFetch(
      { visibility: "private", users: ["bob"], groups: [] },
      () => jsonResponse({ visibility: "public", users: [], groups: [] }),
      () => jsonResponse([shareSummary(partners), shareSummary(auditors)]),
    );
    await renderPanel();

    await screen.findByRole("checkbox", { name: "협력사 B" });
    fireEvent.click(screen.getByRole("radio", { name: "조직 공개" }));
    fireEvent.click(screen.getByRole("button", { name: "열람 범위 저장" }));

    expect(await screen.findByText("열람 범위를 저장했습니다.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "협력사 B" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "감사인" })).not.toBeChecked();
    expect(shareWrites(fetchMock)).toEqual([]);
  });

  it("공유 목록을 못 불러와도 열람 범위는 편집할 수 있다", async () => {
    stubFetch({ visibility: "private", users: [], groups: [] }, undefined, () =>
      jsonResponse({ detail: "일시적으로 요청을 처리할 수 없습니다." }, 500),
    );
    await renderPanel();

    expect(await screen.findByText("일시적으로 요청을 처리할 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "제한" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "열람 범위 저장" })).not.toBeDisabled();
  });

  it("편집 중에는 공유 체크를 막는다", async () => {
    stubFetch({ visibility: "private", users: [], groups: [] }, undefined, () =>
      jsonResponse([shareSummary(partners)]),
    );
    await act(async () => {
      render(<AccessPanel disabled documentId="document-1" onSaved={vi.fn()} />);
    });

    expect(await screen.findByRole("checkbox", { name: "협력사 B" })).toBeDisabled();
  });

  it("화면을 떠나면 조회를 취소한다", () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<AccessPanel documentId="document-1" onSaved={vi.fn()} />);
    unmount();

    expect(fetchMock.mock.calls.map(([url]) => url)).toContain("/api/shares");
    for (const [, init] of fetchMock.mock.calls) expect(init?.signal?.aborted).toBe(true);
  });
});
