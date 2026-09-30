import type {
  ContentType,
  AuthStatus,
  Backlink,
  ClustersResponse,
  DocumentDetail,
  DocumentProgress,
  DocumentSummary,
  DiagnosticsResponse,
  EmbeddingStatus,
  RelatedResponse,
  ResolvedLink,
  SearchResponse,
  SystemStatus,
  TagSuggestionsResponse,
  TextVersionDetail,
  TokenCreated,
  TokenScope,
  TokenSummary,
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

// DB 일시 불가용(503)·네트워크 오류는 기다리면 풀린다 (ADR-048 결정 4). 60초는 #110 B의
// 최악 중단(42초)에 여유를 둔 값이다.
const BACKOFF_START_MS = 1000;
const BACKOFF_CAP_MS = 8000;
const BACKOFF_BUDGET_MS = 60_000;

// 재시도 중인 요청 수. 화면의 재시도 안내(RetryNotice)가 구독한다.
let retryingRequests = 0;
const retryListeners = new Set<() => void>();

export function isRetrying(): boolean {
  return retryingRequests > 0;
}

export function subscribeRetrying(listener: () => void): () => void {
  retryListeners.add(listener);
  return () => {
    retryListeners.delete(listener);
  };
}

function changeRetrying(delta: 1 | -1): void {
  const before = isRetrying();
  retryingRequests += delta;
  if (before !== isRetrying()) for (const listener of retryListeners) listener();
}

// 서버가 멱등키를 지키는 경로 (`retry.py`의 `IDEMPOTENT_CREATE_PATHS`, ADR-047 결정 1).
const IDEMPOTENT_CREATE_PATHS = ["/api/documents", "/api/documents/text"];

/**
 * 서버 미들웨어와 같은 기준이다 — 검색은 메서드만 POST인 읽기다. 문서 생성은 멱등키가 붙어
 * 있을 때만 다시 보낸다 — 서버가 처음 결과를 돌려주므로 안전하다(ADR-047). 다른 쓰기는
 * 헤더가 붙어도 서버가 키를 지키지 않으므로 다시 보내지 않는다.
 */
function isRetryable(path: string, init: RequestInit): boolean {
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "GET" || method === "HEAD" || path === "/api/search") return true;
  return (
    method === "POST" &&
    IDEMPOTENT_CREATE_PATHS.includes(path) &&
    new Headers(init.headers).has("Idempotency-Key")
  );
}

/**
 * 사용자 동작 하나에 키 하나. `crypto.randomUUID`는 보안 컨텍스트(https·localhost)에만 있어
 * 사내망 http 배포에서 없을 수 있다 — `getRandomValues`는 어디서나 있다.
 */
function newIdempotencyKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** 호출자가 취소하면(화면을 떠나면) 기다리지 않고 AbortError로 끝낸다. */
function sleep(ms: number, signal: AbortSignal | null | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * 서버가 알린 최소 대기(ms). RFC 9110 §10.2.3 — 초 수 또는 HTTP 날짜. 없거나 읽을 수 없으면 0.
 */
function retryAfterMs(response: Response): number {
  const value = response.headers.get("Retry-After");
  if (value === null) return 0;
  const ms = /^\d+$/.test(value.trim()) ? Number(value) * 1000 : Date.parse(value) - Date.now();
  return Number.isFinite(ms) ? Math.max(0, ms) : 0;
}

/**
 * 읽기와 멱등키가 있는 쓰기만 지수 백오프 + 전체 지터로 다시 보낸다. 503이 `Retry-After`를
 * 알리면 그보다 일찍 보내지 않는다. 키 없는 쓰기는 첫 시도가 이미 커밋됐을 수 있어
 * 다시 보내면 두 번 실행된다. 재시도는 같은 `init`을 다시 보내므로 키도 같다.
 * 예산을 넘기면 마지막 503 응답(또는 네트워크 오류)을 그대로 돌려준다.
 */
async function fetchWithBackoff(path: string, init: RequestInit): Promise<Response> {
  if (!isRetryable(path, init)) return fetch(path, init);

  const deadline = Date.now() + BACKOFF_BUDGET_MS;
  let attempt = 0;
  let retrying = false;
  try {
    for (;;) {
      let last: Response | TypeError;
      try {
        const response = await fetch(path, init);
        if (response.status !== 503) return response;
        last = response;
      } catch (error) {
        // fetch는 네트워크 실패를 TypeError로만 알린다. 중단(AbortError) 등은 그대로 올린다.
        if (!(error instanceof TypeError)) throw error;
        last = error;
      }
      const backoff = Math.random() * Math.min(BACKOFF_CAP_MS, BACKOFF_START_MS * 2 ** attempt);
      const delay = last instanceof Response ? Math.max(retryAfterMs(last), backoff) : backoff;
      if (Date.now() + delay > deadline) {
        if (last instanceof Response) return last;
        throw last;
      }
      if (!retrying) {
        retrying = true;
        changeRetrying(1);
      }
      await sleep(delay, init.signal);
      attempt += 1;
    }
  } finally {
    if (retrying) changeRetrying(-1);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { parse = true, backoff = true }: { parse?: boolean; backoff?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const response = await (backoff ? fetchWithBackoff : fetch)(path, {
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
  return parse ? (response.json() as Promise<T>) : (undefined as T);
}

export function getAuthStatus(signal?: AbortSignal): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/me", { signal });
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

export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/password", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

export function listTokens(signal?: AbortSignal): Promise<TokenSummary[]> {
  return request<TokenSummary[]>("/api/auth/tokens", { signal });
}

export function createToken(input: {
  name: string;
  scope: TokenScope;
}): Promise<TokenCreated> {
  return request<TokenCreated>("/api/auth/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function revokeToken(id: string): Promise<void> {
  return request<void>(
    `/api/auth/tokens/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    { parse: false },
  );
}

export function listUsers(signal?: AbortSignal): Promise<UserSummary[]> {
  return request<UserSummary[]>("/api/admin/users", { signal });
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
  return request<void>(
    `/api/admin/users/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    { parse: false },
  );
}

export function listDocuments(
  params?: {
    status?: EmbeddingStatus;
    tag?: string;
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal,
): Promise<DocumentSummary[]> {
  const query = new URLSearchParams();
  if (params?.status !== undefined) query.set("status", params.status);
  if (params?.tag !== undefined) query.set("tag", params.tag);
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.size > 0 ? `?${query}` : "";
  return request<DocumentSummary[]>(`/api/documents${suffix}`, { signal });
}

export function getDocumentProgress(signal?: AbortSignal): Promise<DocumentProgress> {
  return request<DocumentProgress>("/api/documents/progress", { signal });
}

export function getDocument(id: string, signal?: AbortSignal): Promise<DocumentDetail> {
  return request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`, { signal });
}

export function getDocumentLinks(id: string, signal?: AbortSignal): Promise<ResolvedLink[]> {
  return request<ResolvedLink[]>(`/api/documents/${encodeURIComponent(id)}/links`, { signal });
}

export function getDocumentBacklinks(id: string, signal?: AbortSignal): Promise<Backlink[]> {
  return request<Backlink[]>(`/api/documents/${encodeURIComponent(id)}/backlinks`, { signal });
}

export function getRelated(id: string, signal?: AbortSignal): Promise<RelatedResponse> {
  return request<RelatedResponse>(`/api/documents/${encodeURIComponent(id)}/related`, { signal });
}

export function getTagSuggestions(
  id: string,
  signal?: AbortSignal,
): Promise<TagSuggestionsResponse> {
  return request<TagSuggestionsResponse>(
    `/api/documents/${encodeURIComponent(id)}/tag-suggestions`,
    { signal },
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

  return request<DocumentSummary>("/api/documents", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": newIdempotencyKey() },
  });
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

export function getDocumentVersion(
  id: string,
  version: number,
  signal?: AbortSignal,
): Promise<TextVersionDetail> {
  return request<TextVersionDetail>(
    `/api/documents/${encodeURIComponent(id)}/versions/${version}`,
    { signal },
  );
}

/** 되감기가 아니라 새 텍스트 버전을 만든다 (ADR-037). */
export function restoreDocumentVersion(
  id: string,
  version: number,
  currentVersion: number,
): Promise<DocumentSummary & { content: string }> {
  return request<DocumentSummary & { content: string }>(
    `/api/documents/${encodeURIComponent(id)}/versions/${version}/restore`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_version: currentVersion }),
    },
  );
}

/** 원본 파일 내려받기 경로. 판을 주지 않으면 최신 판이다. */
export function originalFileUrl(id: string, fileVersion?: number): string {
  const base = `/api/documents/${encodeURIComponent(id)}`;
  return fileVersion === undefined ? `${base}/file` : `${base}/files/${fileVersion}`;
}

/** 이전 판은 지우지 않고 새 판을 쌓는다. 추출 텍스트가 달라지면 새 텍스트 버전이 생긴다. */
export function replaceOriginalFile(
  id: string,
  file: File,
  currentVersion: number,
): Promise<DocumentSummary & { content: string }> {
  const body = new FormData();
  body.append("file", file);
  body.append("current_version", String(currentVersion));
  return request<DocumentSummary & { content: string }>(
    `/api/documents/${encodeURIComponent(id)}/file`,
    { method: "PUT", body },
  );
}

/** changed=false면 추출 결과가 현재 텍스트와 같아 새 버전을 만들지 않았다. */
export function reextractDocument(
  id: string,
  currentVersion: number,
): Promise<DocumentSummary & { content: string; changed: boolean }> {
  return request<DocumentSummary & { content: string; changed: boolean }>(
    `/api/documents/${encodeURIComponent(id)}/reextract`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_version: currentVersion }),
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
    { parse: false },
  );
}

export function reembedDocument(id: string): Promise<DocumentSummary> {
  return request<DocumentSummary>(`/api/documents/${encodeURIComponent(id)}/reembed`, {
    method: "POST",
  });
}

export function search(
  input: {
    query: string;
    tags?: string[];
    contentType?: ContentType | null;
    k?: number;
  },
  signal?: AbortSignal,
): Promise<SearchResponse> {
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
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * 백오프하지 않는다. `/admin/status`는 장애·복구를 보여주는 관측 채널이라 실패를 바로
 * 드러내야 하고, 2초 폴링 자체가 재시도다.
 */
export function getSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  return request<SystemStatus>("/api/system/status", { signal }, { backoff: false });
}

export function getDiagnostics(signal?: AbortSignal): Promise<DiagnosticsResponse> {
  return request<DiagnosticsResponse>("/api/diagnostics", { signal });
}

export function getClusters(signal?: AbortSignal): Promise<ClustersResponse> {
  return request<ClustersResponse>("/api/clusters", { signal });
}
