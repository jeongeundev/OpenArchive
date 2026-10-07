import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "./page";

const auth = vi.hoisted(() => ({ value: { authenticated: true, username: "alice", is_admin: false } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ auth: auth.value, loading: false, setAuth: vi.fn() }),
}));
vi.mock("@/lib/useFolders", () => ({
  useFolders: () => ({ folders: [], loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/useDocuments", () => ({
  useDocuments: () => ({ documents: [], total: 0, loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/useDocumentProgress", () => ({
  useDocumentProgress: () => ({ progress: null, error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listDocumentTags: () => Promise.resolve([]),
}));
// 업로드는 자기 테스트가 있다. 부여 대상 조회 같은 요청을 끌어오지 않게 뺀다.
vi.mock("@/components/UploadDropzone", () => ({ UploadDropzone: () => null }));

afterEach(() => {
  auth.value = { authenticated: true, username: "alice", is_admin: false };
});

describe("문서 화면 사이드바", () => {
  it("로그인 사용자에게 폴더 트리 아래 휴지통 링크를 보인다", () => {
    render(<Home />);

    const link = screen.getByRole("link", { name: "휴지통" });
    expect(link).toHaveAttribute("href", "/trash");
    const tree = screen.getByRole("button", { name: "전체 문서" });
    expect(tree.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("로그인하지 않았으면 휴지통 링크가 없다", () => {
    auth.value = { authenticated: false, username: null as unknown as string, is_admin: false };

    render(<Home />);

    expect(screen.queryByRole("link", { name: "휴지통" })).toBeNull();
  });
});
