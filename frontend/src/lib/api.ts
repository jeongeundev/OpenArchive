import type {
  ContentType,
  DocumentDetail,
  DocumentSummary,
  EmbeddingStatus,
  SearchResponse,
  SystemStatus,
  Visibility,
} from "./types";
import { getCurrentUser } from "./user";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly currentVersion?: number;

  constructor(status: number, detail: string, currentVersion?: number) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.currentVersion = currentVersion;
  }
}

function userHeaders(): Headers {
  const headers = new Headers();
  const user = getCurrentUser();
  if (user !== null) {
    headers.set("X-User-Id", user);
  }
  return headers;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  parseResponse = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [name, value] of userHeaders()) {
    headers.set(name, value);
  }

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let errorBody: unknown;
    try {
      errorBody = await response.json();
    } catch {
      errorBody = null;
    }

    const body = errorBody as { detail?: unknown; current_version?: unknown } | null;
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : `요청에 실패했습니다. (${response.status})`;
    const currentVersion =
      response.status === 409 && typeof body?.current_version === "number"
        ? body.current_version
        : undefined;
    throw new ApiError(response.status, detail, currentVersion);
  }
  return parseResponse ? (response.json() as Promise<T>) : (undefined as T);
}

export function listDocuments(params?: {
  status?: EmbeddingStatus;
  tag?: string;
}): Promise<DocumentSummary[]> {
  const query = new URLSearchParams();
  if (params?.status !== undefined) query.set("status", params.status);
  if (params?.tag !== undefined) query.set("tag", params.tag);
  const suffix = query.size > 0 ? `?${query}` : "";
  return request<DocumentSummary[]>(`/api/documents${suffix}`);
}

export function getDocument(id: string): Promise<DocumentDetail> {
  return request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`);
}

export function uploadDocument(input: {
  file: File;
  title?: string;
  tags: string[];
  visibility: Visibility;
}): Promise<DocumentSummary> {
  const body = new FormData();
  body.append("file", input.file);
  if (input.title !== undefined) body.append("title", input.title);
  for (const tag of input.tags) body.append("tags", tag);
  body.append("visibility", input.visibility);

  return request<DocumentSummary>("/api/documents", { method: "POST", body });
}

export function editDocument(
  id: string,
  input: { content: string; version: number },
): Promise<DocumentSummary & { content: string }> {
  return request<DocumentSummary & { content: string }>(
    `/api/documents/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
}

export function deleteDocument(id: string): Promise<void> {
  return request<void>(
    `/api/documents/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    false,
  );
}

export function reembedDocument(id: string): Promise<DocumentSummary> {
  return request<DocumentSummary>(`/api/documents/${encodeURIComponent(id)}/reembed`, {
    method: "POST",
  });
}

export function search(input: {
  query: string;
  tags?: string[];
  contentType?: ContentType | null;
  k?: number;
}): Promise<SearchResponse> {
  const body: {
    query: string;
    tags?: string[];
    content_type?: ContentType;
    k?: number;
  } = { query: input.query };
  if (input.tags !== undefined) body.tags = input.tags;
  if (input.contentType != null) body.content_type = input.contentType;
  if (input.k !== undefined) body.k = input.k;

  return request<SearchResponse>("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/system/status");
}
