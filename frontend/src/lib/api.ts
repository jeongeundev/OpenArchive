import type {
  ContentType,
  AuthStatus,
  DocumentDetail,
  DocumentSummary,
  DiagnosticsResponse,
  EmbeddingStatus,
  RelatedResponse,
  SearchResponse,
  SystemStatus,
  TagSuggestionsResponse,
  Visibility,
  UserSummary,
} from "./types";

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

async function request<T>(
  path: string,
  init: RequestInit = {},
  parseResponse = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers,
  });
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

export function getAuthStatus(): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/me");
}

export function login(username: string, password: string): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/logout", { method: "POST" });
}

export function listUsers(): Promise<UserSummary[]> {
  return request<UserSummary[]>("/api/admin/users");
}

export function createUser(input: {
  username: string;
  password: string;
  is_admin: boolean;
}): Promise<UserSummary> {
  return request<UserSummary>("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function deleteUser(id: string): Promise<void> {
  return request<void>(`/api/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" }, false);
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

export function getRelated(id: string): Promise<RelatedResponse> {
  return request<RelatedResponse>(`/api/documents/${encodeURIComponent(id)}/related`);
}

export function getTagSuggestions(id: string): Promise<TagSuggestionsResponse> {
  return request<TagSuggestionsResponse>(
    `/api/documents/${encodeURIComponent(id)}/tag-suggestions`,
  );
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

export function updateTags(id: string, tags: string[]): Promise<DocumentSummary> {
  return request<DocumentSummary>(`/api/documents/${encodeURIComponent(id)}/tags`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
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
  // 빈 배열은 보내지 않는다. 백엔드 SQL이 "필터 없음"을 NULL로만 표현하므로,
  // 빈 배열을 넘기면 d.tags && '{}' 가 항상 거짓이 되어 결과가 0건이 된다.
  if (input.tags !== undefined && input.tags.length > 0) body.tags = input.tags;
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

export function getDiagnostics(): Promise<DiagnosticsResponse> {
  return request<DiagnosticsResponse>("/api/diagnostics");
}
