// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "./page";
import RootPage from "../page";

const auth = vi.hoisted(() => ({ value: { authenticated: true, username: "alice", is_admin: false } }));
const location = vi.hoisted(() => ({ search: "" }));
const progressState = vi.hoisted(() => ({ value: null as import("@/lib/types").DocumentProgress | null }));
const folderList = vi.hoisted(() => ({ value: [] as import("@/lib/types").Folder[] }));
const typeCounts = vi.hoisted(() => ({ value: {} as Record<string, number> }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(location.search),
}));
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ auth: auth.value, loading: false, setAuth: vi.fn() }),
}));
vi.mock("@/lib/useFolders", () => ({
  useFolders: () => ({ folders: folderList.value, loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/useDocuments", () => ({
  useDocuments: () => ({ documents: [], total: 0, loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/useDocumentProgress", () => ({
  useDocumentProgress: () => ({ progress: progressState.value, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listDocumentTags: () => Promise.resolve([]),
  countDocuments: (filters: { contentType?: string }) => Promise.resolve(typeCounts.value[filters.contentType ?? ""] ?? 0),
}));
// 업로드는 자기 테스트가 있다. 부여 대상 조회 같은 요청을 끌어오지 않게 뺀다.
// 펼침 여부만 보면 되므로 표지 요소 하나로 둔다.
vi.mock("@/components/UploadDropzone", () => ({
  UploadDropzone: ({ onUploaded }: { onUploaded: () => void }) => (
    <div data-testid="upload-dropzone"><button type="button" onClick={onUploaded}>업로드 완료 흉내</button></div>
  ),
}));

afterEach(() => {
  auth.value = { authenticated: true, username: "alice", is_admin: false };
  location.search = "";
  folderList.value = [];
  progressState.value = null;
  typeCounts.value = {};
});

describe("유형 칩", () => {
  it("데이터에 있는 유형만 칩으로 보이고 jpeg도 빼지 않는다", async () => {
    typeCounts.value = { pdf: 2, jpeg: 1 };

    render(<Home />);

    const group = screen.getByRole("group", { name: "문서 유형" });
    expect(await within(group).findByRole("button", { name: "JPEG" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "PDF" })).toBeInTheDocument();
    expect(within(group).queryByRole("button", { name: "JPG" })).toBeNull();
  });
});

describe("폴더 주소", () => {
  it("`/documents?folder=<id>`로 들어오면 그 폴더가 열린 상태다 — 부서 링크가 이 경로를 쓴다 (D16)", () => {
    location.search = "folder=f1";
    folderList.value = [{
      id: "f1", parent_id: null, name: "IT전략실", created_by: "kim", document_count: 0,
      scope: { visibility: "public", users: [], groups: [] }, inherited: false,
      can_manage: false, can_change_access: false, can_share: false,
    }];

    render(<Home />);

    expect(screen.getByRole("heading", { level: 2, name: "IT전략실" })).toBeInTheDocument();
  });
});

describe("문서 화면 사이드바", () => {
  it("로그인 사용자에게 폴더 트리 위의 탭에 휴지통 링크를 보인다", () => {
    render(<Home />);

    const link = screen.getByRole("link", { name: "휴지통" });
    expect(link).toHaveAttribute("href", "/trash");
    const tree = within(screen.getByRole("navigation", { name: "폴더" })).getByRole("button", { name: "전체 문서" });
    expect(link.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("로그인하지 않았으면 휴지통 링크가 없다", () => {
    auth.value = { authenticated: false, username: null as unknown as string, is_admin: false };

    render(<Home />);

    expect(screen.queryByRole("link", { name: "휴지통" })).toBeNull();
  });
});

describe("업로드 폼 접기", () => {
  it("로그인 사용자의 첫 렌더에는 업로드 폼이 접혀 있고 「문서 올리기」 버튼이 있다", () => {
    render(<Home />);

    const button = screen.getByRole("button", { name: "문서 올리기" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const controlsId = button.getAttribute("aria-controls");
    expect(controlsId).toBeTruthy();
    expect(screen.queryByTestId("upload-dropzone")).toBeNull();
  });

  it("버튼을 누르면 폼이 펼쳐지고 버튼은 「업로드 닫기」가 된다", () => {
    render(<Home />);

    const button = screen.getByRole("button", { name: "문서 올리기" });
    fireEvent.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveTextContent("업로드 닫기");
    const region = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(region).not.toBeNull();
    expect(region).toContainElement(screen.getByTestId("upload-dropzone"));
  });

  it("업로드가 끝나도 폼은 펼친 채로 남는다", () => {
    render(<Home />);

    fireEvent.click(screen.getByRole("button", { name: "문서 올리기" }));
    fireEvent.click(screen.getByRole("button", { name: "업로드 완료 흉내" }));

    expect(screen.getByRole("button", { name: "업로드 닫기" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("upload-dropzone")).toBeInTheDocument();
  });

  it("다시 누르면 접힌다", () => {
    render(<Home />);

    fireEvent.click(screen.getByRole("button", { name: "문서 올리기" }));
    fireEvent.click(screen.getByRole("button", { name: "업로드 닫기" }));

    expect(screen.getByRole("button", { name: "문서 올리기" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("upload-dropzone")).toBeNull();
  });

  it("익명 사용자에게는 버튼도 폼도 없다", () => {
    auth.value = { authenticated: false, username: null as unknown as string, is_admin: false };

    render(<Home />);

    expect(screen.queryByRole("button", { name: "문서 올리기" })).toBeNull();
    expect(screen.queryByTestId("upload-dropzone")).toBeNull();
  });
});

it("문서 목록은 새 홈과 별도 화면으로 제공한다", () => {
  expect(RootPage).not.toBe(Home);
});

it("홈의 upload=1에서 업로드 폼을 펼친다", () => {
  location.search = "upload=1";
  render(<Home />);
  expect(screen.getByTestId("upload-dropzone")).toBeInTheDocument();
});

describe("처리 현황 표시", () => {
  const idle = { extracting: 0, extraction_failed: 0, pending: 0, processing: 0, ready: 4, error: 0 };
  it("처리 중인 문서가 있을 때만 표 위에 진행률을 표시한다", () => {
    progressState.value = { ...idle, pending: 1, processing: 2 };
    const { rerender } = render(<Home />);
    expect(screen.getByText("내가 열람할 수 있는 문서 7건 · 처리 중 3")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "검색 준비 진행률" })).toHaveAttribute("aria-valuenow", "4");
    progressState.value = idle;
    rerender(<Home />);
    expect(screen.getByText("내가 열람할 수 있는 문서 4건 · 모두 검색 가능")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
  it("실패한 문서가 있으면 모두 검색 가능하다고 안내하지 않는다", () => {
    progressState.value = { ...idle, error: 1 };
    render(<Home />);
    expect(screen.getByText("내가 열람할 수 있는 문서 5건")).toBeInTheDocument();
    expect(screen.queryByText(/모두 검색 가능/)).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
