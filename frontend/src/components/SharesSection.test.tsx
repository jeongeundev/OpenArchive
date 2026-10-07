import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SharesSection } from "./SharesSection";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const partner = {
  id: "s-1",
  name: "B사 협업",
  created_at: "2026-10-02T00:00:00Z",
  documents: [{ id: "d-1", title: "제품 소개서" }],
  tokens: [{ id: "t-1", name: "B사 MCP", scope: "read", created_at: "2026-10-02T00:00:00Z", expires_at: null, last_used_at: null, expired: false }],
};

type Route = (url: string, method: string, init?: RequestInit) => Response | undefined;

/** 목록 응답은 호출할 때마다 다음 값으로 넘어가고, 마지막 값을 유지한다. */
function routedFetch(listSequence: unknown[], extra: Route = () => undefined) {
  let listCalls = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const custom = extra(url, method, init);
    if (custom !== undefined) return custom;
    if (url === "/api/shares" && method === "GET") {
      const body = listSequence[Math.min(listCalls, listSequence.length - 1)];
      listCalls += 1;
      return response(body);
    }
    return new Response(null, { status: 204 });
  });
}

function calls(fetchMock: ReturnType<typeof routedFetch>, method: string, url: string) {
  return fetchMock.mock.calls.filter(
    ([input, init]) => String(input) === url && (init?.method ?? "GET") === method,
  );
}

describe("외부 공유 절", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("공유의 이름·포함 문서(상세 링크)·토큰 이름을 보여 준다", async () => {
    vi.stubGlobal("fetch", routedFetch([[partner]]));

    render(<SharesSection />);

    const card = await screen.findByRole("region", { name: "B사 협업" });
    const link = within(card).getByRole("link", { name: "제품 소개서" });
    expect(link).toHaveAttribute("href", "/documents/d-1");
    expect(within(card).getByText("B사 MCP")).toBeInTheDocument();
  });

  it("공유가 없으면 빈 상태를 알린다", async () => {
    vi.stubGlobal("fetch", routedFetch([[]]));

    render(<SharesSection />);

    expect(await screen.findByText("아직 공유가 없습니다.")).toBeInTheDocument();
  });

  it("안내 문구가 읽기 전용·조직 공개 문서도 열림·문서 상세에서 넣기를 알린다", async () => {
    vi.stubGlobal("fetch", routedFetch([[]]));

    render(<SharesSection />);

    await screen.findByText("아직 공유가 없습니다.");
    expect(screen.getByText(/읽기 전용/)).toBeInTheDocument();
    expect(screen.getByText(/공유에 넣은 문서만/)).toBeInTheDocument();
    expect(screen.getByText(/조직 공개 문서도 공유에 넣으면 외부에 열립니다/)).toBeInTheDocument();
    expect(screen.getByText(/문서 상세의 「열람 범위」/)).toBeInTheDocument();
  });

  it("이름으로 공유를 만들면 목록에 나타난다", async () => {
    const created = { ...partner, id: "s-2", name: "C사", documents: [], tokens: [] };
    const fetchMock = routedFetch([[], [created]], (url, method) =>
      url === "/api/shares" && method === "POST" ? response(created, 201) : undefined,
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SharesSection />);
    await screen.findByText("아직 공유가 없습니다.");
    fireEvent.change(screen.getByRole("textbox", { name: "공유 이름" }), {
      target: { value: "C사" },
    });
    fireEvent.click(screen.getByRole("button", { name: "공유 만들기" }));

    expect(await screen.findByRole("region", { name: "C사" })).toBeInTheDocument();
    const [post] = calls(fetchMock, "POST", "/api/shares");
    expect(JSON.parse(post[1]?.body as string)).toEqual({ name: "C사" });
  });

  it("만들기 실패의 detail을 그대로 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      routedFetch([[partner]], (url, method) =>
        url === "/api/shares" && method === "POST"
          ? response({ detail: "같은 이름의 공유가 이미 있습니다." }, 409)
          : undefined,
      ),
    );

    render(<SharesSection />);
    await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.change(screen.getByRole("textbox", { name: "공유 이름" }), {
      target: { value: "B사 협업" },
    });
    fireEvent.click(screen.getByRole("button", { name: "공유 만들기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("같은 이름의 공유가 이미 있습니다.");
  });

  it("포함 문서를 빼면 목록에서 사라진다", async () => {
    const fetchMock = routedFetch([[partner], [{ ...partner, documents: [] }]]);
    vi.stubGlobal("fetch", fetchMock);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.click(within(card).getByRole("button", { name: "제품 소개서 빼기" }));

    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "제품 소개서" })).not.toBeInTheDocument(),
    );
    expect(calls(fetchMock, "DELETE", "/api/shares/s-1/documents/d-1")).toHaveLength(1);
  });

  it("발급한 토큰 원문은 한 번만 보이고, 다른 동작 뒤에는 사라진다", async () => {
    const issued = {
      id: "t-2",
      name: "C사 연동",
      scope: "read",
      created_at: "2026-10-02T01:00:00Z", expires_at: null, last_used_at: null, expired: false,
      token: "share-plaintext-once",
    };
    const withNew = {
      ...partner,
      tokens: [...partner.tokens, { id: "t-2", name: "C사 연동", scope: "read", created_at: issued.created_at, expires_at: null, last_used_at: null, expired: false }],
    };
    const fetchMock = routedFetch([[partner], [withNew], [{ ...withNew, documents: [] }]], (url, method) =>
      url === "/api/shares/s-1/tokens" && method === "POST" ? response(issued, 201) : undefined,
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.change(within(card).getByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "C사 연동" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "공유 토큰 발급" }));

    expect(await screen.findByText("share-plaintext-once")).toBeInTheDocument();
    expect(screen.getByText(/다시 볼 수 없습니다/)).toBeInTheDocument();
    expect(await within(card).findByText("C사 연동")).toBeInTheDocument();
    const [post] = calls(fetchMock, "POST", "/api/shares/s-1/tokens");
    expect(JSON.parse(post[1]?.body as string)).toEqual({ name: "C사 연동" });

    fireEvent.click(within(card).getByRole("button", { name: "제품 소개서 빼기" }));
    await waitFor(() =>
      expect(screen.queryByText("share-plaintext-once")).not.toBeInTheDocument(),
    );
    // 목록에는 원문 없이 이름만 남는다
    expect(within(card).getByText("C사 연동")).toBeInTheDocument();
  });

  it("공유 토큰도 만료일을 골라 발급하면 그날 끝까지 유효한 시각을 보낸다", async () => {
    const issued = {
      id: "t-2",
      name: "기한부",
      scope: "read",
      created_at: "2026-10-08T00:00:00Z",
      expires_at: new Date(2026, 10, 1).toISOString(),
      last_used_at: null,
      expired: false,
      token: "share-plaintext-once",
    };
    const fetchMock = routedFetch([[partner]], (url, method) =>
      url === "/api/shares/s-1/tokens" && method === "POST" ? response(issued, 201) : undefined,
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.change(within(card).getByRole("textbox", { name: "토큰 이름" }), {
      target: { value: "기한부" },
    });
    const date = within(card).getByLabelText("만료일 (선택)");
    expect(date).toHaveAttribute("type", "date");
    fireEvent.change(date, { target: { value: "2026-10-31" } });
    fireEvent.click(within(card).getByRole("button", { name: "공유 토큰 발급" }));

    expect(await screen.findByText("share-plaintext-once")).toBeInTheDocument();
    const [post] = calls(fetchMock, "POST", "/api/shares/s-1/tokens");
    expect(JSON.parse(post[1]?.body as string)).toEqual({
      name: "기한부",
      expires_at: new Date(2026, 10, 1).toISOString(),
    });
  });

  it("토큰 줄에 서버가 판정한 「만료」와 마지막 사용을 보인다 (TC 276)", async () => {
    const share = {
      ...partner,
      tokens: [
        {
          id: "t-1",
          name: "B사 MCP",
          scope: "read",
          created_at: "2026-10-02T00:00:00Z",
          expires_at: "2026-10-05T00:00:00Z",
          last_used_at: "2026-10-04T03:00:00Z",
          expired: true,
        },
        {
          id: "t-2",
          name: "B사 REST",
          scope: "read",
          created_at: "2026-10-02T00:00:00Z",
          expires_at: null,
          last_used_at: null,
          expired: false,
        },
      ],
    };
    vi.stubGlobal("fetch", routedFetch([[share]]));

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });

    const expiredItem = within(card).getByText("B사 MCP").closest("li");
    const liveItem = within(card).getByText("B사 REST").closest("li");
    if (expiredItem === null || liveItem === null) throw new Error("토큰 줄이 없다");
    expect(within(expiredItem).getByText("만료")).toBeInTheDocument();
    expect(within(expiredItem).getByText(/마지막 사용/)).toBeInTheDocument();
    expect(within(expiredItem).queryByText(/사용 기록 없음/)).not.toBeInTheDocument();
    expect(within(liveItem).queryByText("만료")).not.toBeInTheDocument();
    expect(within(liveItem).getByText(/만료 없음/)).toBeInTheDocument();
    expect(within(liveItem).getByText(/사용 기록 없음/)).toBeInTheDocument();
  });

  it("토큰을 폐기하면 목록에서 사라진다", async () => {
    const fetchMock = routedFetch([[partner], [{ ...partner, tokens: [] }]]);
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.click(within(card).getByRole("button", { name: "B사 MCP 폐기" }));

    await waitFor(() => expect(screen.queryByText("B사 MCP")).not.toBeInTheDocument());
    expect(calls(fetchMock, "DELETE", "/api/shares/s-1/tokens/t-1")).toHaveLength(1);
  });

  it("공유 삭제는 확인을 거치고, 취소하면 지우지 않는다", async () => {
    const fetchMock = routedFetch([[partner], []]);
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.click(within(card).getByRole("button", { name: "공유 삭제" }));

    expect(confirm).toHaveBeenCalledTimes(1);
    const message = confirm.mock.calls[0][0] as string;
    expect(message).toMatch(/토큰이 모두 무효/);
    expect(message).toMatch(/외부에서 더 이상 볼 수 없/);
    expect(calls(fetchMock, "DELETE", "/api/shares/s-1")).toHaveLength(0);
    expect(screen.getByRole("region", { name: "B사 협업" })).toBeInTheDocument();
  });

  it("확인하면 공유를 지운다", async () => {
    const fetchMock = routedFetch([[partner], []]);
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SharesSection />);
    const card = await screen.findByRole("region", { name: "B사 협업" });
    fireEvent.click(within(card).getByRole("button", { name: "공유 삭제" }));

    expect(await screen.findByText("아직 공유가 없습니다.")).toBeInTheDocument();
    expect(calls(fetchMock, "DELETE", "/api/shares/s-1")).toHaveLength(1);
  });

  it("목록을 불러오지 못하면 빈 목록이 아니라 오류를 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      routedFetch([[]], (url, method) =>
        url === "/api/shares" && method === "GET"
          ? response({ detail: "공유 목록 조회 실패" }, 500)
          : undefined,
      ),
    );

    render(<SharesSection />);

    expect(await screen.findByRole("alert")).toHaveTextContent("공유 목록 조회 실패");
    expect(screen.queryByText("아직 공유가 없습니다.")).not.toBeInTheDocument();
  });
});
