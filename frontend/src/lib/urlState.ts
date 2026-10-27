// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { DocumentFilters } from "./api";
import { MAX_K, SUPPORTED_CONTENT_TYPES, type ContentType } from "./types";
import type { SearchInput } from "./useSearch";

// 검색·목록 상태를 주소에 둔다 — 문서로 갔다가 뒤로 오면 같은 조건으로 다시 그린다. 기본값은 주소에 넣지 않는다.

const DEFAULT_K = 10;

function contentType(value: string | null): ContentType | null {
  return SUPPORTED_CONTENT_TYPES.find((type) => type === value) ?? null;
}

function withQuery(path: string, params: URLSearchParams): string {
  return params.size > 0 ? `${path}?${params}` : path;
}

export function searchHref(input: SearchInput): string {
  const params = new URLSearchParams({ q: input.query });
  if (input.tags.length > 0) params.set("tags", input.tags.join(","));
  if (input.contentType !== null) params.set("type", input.contentType);
  if (input.folderId !== null) params.set("folder", input.folderId);
  if (input.k !== DEFAULT_K) params.set("k", String(input.k));
  return withQuery("/search", params);
}

/** 주소에 검색어가 없으면 복원할 검색이 없다. 손으로 고친 값은 폼이 보낼 수 있는 값으로 되돌린다. */
export function searchInputFromParams(params: URLSearchParams): SearchInput | null {
  const query = params.get("q")?.trim() ?? "";
  if (!query) return null;
  const k = Number.parseInt(params.get("k") ?? "", 10);
  return {
    query,
    tags: (params.get("tags") ?? "").split(",").map((tag) => tag.trim()).filter(Boolean),
    contentType: contentType(params.get("type")),
    folderId: params.get("folder") || null,
    k: Number.isNaN(k) ? DEFAULT_K : Math.min(MAX_K, Math.max(1, k)),
  };
}

export interface ListState {
  folderId: string | null;
  filters: Pick<DocumentFilters, "q" | "contentType" | "tag" | "sort">;
  /** 0부터 센다. 주소에는 1부터 쓴다. */
  page: number;
  /** 「부여받은 문서」 보기. 폴더와 함께 쓰지 않는다 (D20). */
  granted?: boolean;
}

export function listHref({ folderId, filters, page, granted }: ListState): string {
  const params = new URLSearchParams();
  if (granted) params.set("granted", "1");
  else if (folderId !== null) params.set("folder", folderId);
  if (filters.q?.trim()) params.set("q", filters.q);
  if (filters.contentType) params.set("type", filters.contentType);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.sort && filters.sort !== "updated") params.set("sort", filters.sort);
  if (page > 0) params.set("page", String(page + 1));
  return withQuery("/", params);
}

export function listStateFromParams(params: URLSearchParams): ListState {
  const filters: ListState["filters"] = {};
  const q = params.get("q");
  if (q?.trim()) filters.q = q;
  const type = contentType(params.get("type"));
  if (type !== null) filters.contentType = type;
  const tag = params.get("tag");
  if (tag) filters.tag = tag;
  filters.sort = params.get("sort") === "title" ? "title" : "updated";
  const page = Number(params.get("page"));
  const granted = params.get("granted") === "1";
  return {
    folderId: granted ? null : params.get("folder") || null,
    filters,
    page: Number.isInteger(page) && page >= 1 ? page - 1 : 0,
    ...(granted ? { granted } : {}),
  };
}

// 탭마다 따로인 편의 기능이다. 저장소를 쓸 수 없으면(차단·비공개 창) 첫 목록으로 돌아갈 뿐이다.
const LAST_LIST_KEY = "openarchive:last-list";

export function rememberListHref(href: string): void {
  try {
    sessionStorage.setItem(LAST_LIST_KEY, href);
  } catch {
    // 기억하지 못해도 목록은 그대로 동작한다.
  }
}

export function lastListHref(): string {
  try {
    const href = sessionStorage.getItem(LAST_LIST_KEY);
    // 목록 주소만 받는다 — 링크 href로 쓰이므로 다른 곳을 가리키는 값을 그대로 따르지 않는다.
    if (href === "/" || href?.startsWith("/?")) return href;
  } catch {
    // 아래로.
  }
  return "/";
}
