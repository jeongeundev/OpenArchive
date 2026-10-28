// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
export type EmbeddingStatus = "pending" | "processing" | "ready" | "error";
export type ContentType =
  "pdf" | "docx" | "txt" | "md" | "hwp" | "hwpx" | "xlsx" | "pptx" | "png" | "jpg" | "jpeg";
/** 워커가 원본에서 텍스트를 인식(OCR)하는 상태. 인식 대상이 아닌 문서는 처음부터 `done`이다 (ADR-052). */
export type ExtractionStatus = "pending" | "failed" | "done";
export type Visibility = "public" | "private";

/** 값은 서버 계약(PyPI 0.1.0)이라 그대로 두고 화면 문구만 바꾼다 — private은 소유자 + 부여 대상이다 (ADR-044). */
export const VISIBILITY_LABEL: Record<Visibility, string> = {
  public: "조직 공개",
  private: "제한",
};

export interface FolderScope {
  visibility: Visibility;
  users: string[];
  groups: string[];
}

/** 소유자의 소속 부서. 부서 폴더는 볼 수 있을 때만 id가 온다 (D16). */
export interface OwnerDepartment {
  name: string;
  folder_id: string | null;
}

export interface Folder {
  /** 이 폴더가 부서 폴더면 그 부서 — 최상위 폴더만 지정된다 (D15). */
  department?: { id: string; name: string } | null;
  id: string;
  parent_id: string | null;
  name: string;
  created_by: string;
  document_count: number;
  scope: FolderScope;
  inherited: boolean;
  can_manage: boolean;
  can_change_access: boolean;
  /** 요청자가 이 폴더의 최상위 폴더를 만든 사람인가 — 그 사람만 공유에 넣고 뺀다. */
  can_share: boolean;
}

export interface FolderPathItem { id: string; name: string }
export interface DocumentFolder extends FolderPathItem { path: FolderPathItem[] }

export type TokenScope = "read" | "read_write";

export interface TokenSummary {
  id: string;
  name: string;
  scope: TokenScope;
  created_at: string;
  /** 만료 시각. null이면 만료 없음 (ADR-061 결정 1). */
  expires_at: string | null;
  /** 성공한 요청의 마지막 사용 시각(1분 단위로 갱신). 없으면 null. */
  last_used_at: string | null;
  /** 서버가 DB 시각으로 판정한 만료 여부 — 브라우저 시계로 다시 계산하지 않는다. */
  expired: boolean;
}

/** 원문 `token`은 발급 응답에만 있다. 목록에는 없다. */
export interface TokenCreated extends TokenSummary {
  token: string;
}

export interface ShareDocument {
  id: string;
  title: string;
}

export interface ShareFolder {
  id: string;
  name: string;
}

/** 공유 토큰은 읽기 전용이다 (ADR-044 결정 3). */
export interface ShareTokenSummary {
  id: string;
  name: string;
  scope: "read";
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  expired: boolean;
}

/** 원문 `token`은 발급 응답에만 있다. 공유 목록의 토큰에는 없다. */
export type ShareTokenCreated = ShareTokenSummary & { token: string };

/** 외부 협업 주체. 소유자 자신만 보고 관리한다 (ADR-044 「공유」). */
export interface ShareSummary {
  id: string;
  name: string;
  created_at: string;
  documents: ShareDocument[];
  /** 공유에 넣은 폴더. 하위 폴더의 「폴더 범위 따름」 문서가 함께 공유된다. */
  folders: ShareFolder[];
  tokens: ShareTokenSummary[];
}

export interface DocumentSummary {
  owner_department?: OwnerDepartment | null;
  follows_folder: boolean;
  id: string;
  title: string;
  filename: string | null;
  content_type: ContentType;
  version: number;
  owner_id: string;
  /** 문서 자신의 공개범위. 폴더로 만든 문서는 private로 닫혀 있다 (ADR-054). */
  visibility: Visibility;
  /** 실제로 적용되는 공개범위 — 「폴더 범위 따름」이면 최상위 폴더의 값. 화면 표시는 이것을 쓴다. */
  effective_visibility: Visibility;
  tags: string[];
  embedding_status: EmbeddingStatus;
  extraction_status: ExtractionStatus;
  created_at: string;
  updated_at: string;
  /** 볼 수 있는 폴더 경로(최상위부터). 폴더가 없거나 볼 수 없으면 null. 목록 API만 싣는다. */
  folder_path?: string[] | null;
}

/** 열람 범위 안 문서의 파이프라인 단계별 수. 인식이 끝난 문서만 임베딩 단계로 센다 (ADR-052). */
export interface DocumentProgress {
  extracting: number;
  extraction_failed: number;
  pending: number;
  processing: number;
  ready: number;
  error: number;
}

export interface TextVersion {
  version: number;
  created_at: string;
  /** 표시 문구는 화면이 정한다. */
  author: string | null;
  author_via: string | null;
}

export interface DiffLine {
  op: "equal" | "added" | "removed";
  text: string;
}

export interface DiffHunk {
  lines: DiffLine[];
}

export interface VersionDiff {
  base: number;
  target: number;
  identical: boolean;
  /** 서버가 비교 상한을 넘어 계산하지 않았다. 그때 hunks는 비어 있다. */
  too_large: boolean;
  hunks: DiffHunk[];
}

export interface TextVersionDetail extends TextVersion {
  content: string;
  /** `?chunk=`로 물은 대목의 위치(UTF-16 단위). 번호에 맞는 청크가 없으면 null이다. */
  passage_start?: number | null;
  passage_end?: number | null;
}

/** 원본 파일 한 판의 메타데이터. 바이트는 내려받기 경로로만 받는다. */
export interface OriginalFile {
  file_version: number;
  filename: string;
  size: number;
  sha256: string;
  /** 이 판의 텍스트가 아직 인식되지 않았으면 null이다. */
  text_version: number | null;
  /**
   * 서버가 판정한 미리보기 상태(ADR-058). ready만 미리보기를 열 수 있다 — 화면은 확장자로 다시 판정하지 않는다.
   * 미리보기 대상이 아닌 형식이면 null.
   */
  preview_status: "ready" | "pending" | "failed" | "unavailable" | null;
  uploaded_by: string;
  uploaded_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  folder: DocumentFolder | null;
  /** 소유자에게만 참 — 볼 수 없는 폴더 안에 든 자기 문서. 폴더 정보는 오지 않는다. */
  hidden_folder?: boolean;
  content: string;
  versions: TextVersion[];
  files: OriginalFile[];
  chunk_count: number;
  chunk_version: number | null;
}

export interface ResolvedLink {
  title: string;
  document_id: string | null;
}

export interface Backlink {
  document_id: string;
  title: string;
}

export interface SearchPassage {
  chunk_index: number;
  content: string;
  based_on_version: number;
  score: number;
}

export interface SearchResult {
  owner_department?: OwnerDepartment | null;
  document_id: string;
  title: string;
  filename: string | null;
  tags: string[];
  content_type: ContentType;
  chunk_index: number;
  content: string;
  score: number;
  based_on_version: number;
  via: SearchVia | null;
  folder_path?: string[] | null;
  owner?: string | null;
  updated_at?: string | null;
  preview?: string | null;
  passages?: SearchPassage[];
}

export interface SearchVia {
  from_document_id: string;
  from_title: string;
  kind: string;
  depth: number;
}

export interface SearchResponse {
  items: SearchResult[];
  sql: string;
}

/** 근거 기반 답변의 근거 하나 — 모델에 준 대목이며 `cited`가 실제 인용 여부다 (ADR-043). */
export interface AnswerSource {
  owner_department?: OwnerDepartment | null;
  label: number;
  document_id: string;
  title: string;
  chunk_index: number;
  based_on_version: number;
  current_version: number;
  revised: boolean;
  content: string;
  cited: boolean;
  content_type?: ContentType | null;
  folder_path?: string[] | null;
  owner?: string | null;
}

export interface AskResponse {
  status: "answered" | "no_evidence" | "disabled" | "failed";
  answer: string | null;
  detail: string | null;
  sources: AnswerSource[];
  items: SearchResult[];
}

export interface RelatedDocument {
  mutual: boolean;
  document_id: string;
  title: string;
  tags: string[];
  kind: string;
  score: number;
}

export interface IdenticalDocument {
  document_id: string;
  title: string;
}

export interface RelatedResponse {
  items: RelatedDocument[];
  identical: IdenticalDocument[];
  based_on_version: number | null;
  reason: string | null;
}

export interface TagSuggestion {
  tag: string;
  freq: number;
}

export interface TagSuggestionsResponse {
  items: TagSuggestion[];
  based_on_version: number | null;
  reason: string | null;
}

/** 정리 추천 (ADR-064): 관계 이웃 total개 중 votes개가 든 폴더. */
export interface FolderSuggestion {
  folder_id: string;
  path: FolderPathItem[];
  votes: number;
  total: number;
}

export interface FolderSuggestionResponse {
  suggestion: FolderSuggestion | null;
}

export interface UnfiledDocument {
  document_id: string;
  title: string;
  suggestion: FolderSuggestion | null;
}

export interface UnfiledResponse {
  count: number;
  items: UnfiledDocument[];
}

export interface JobCounts {
  pending: number;
  processing: number;
  recovery_pending: number;
  error: number;
}

export interface SystemStatus {
  node_address: string | null;
  node_port: number;
  jobs: JobCounts;
  job_lease_seconds: number;
  last_job_finished_at: string | null;
  inconsistent_documents: number;
  stale_edge_documents: number;
  extraction_pending: number;
  extraction_failed: number;
  preview_pending: number;
  preview_failed: number;
  preview_unavailable: number;
  embedding_provider: string;
}

export interface AuthStatus {
  department?: OwnerDepartment | null;
  authenticated: boolean;
  username: string | null;
  is_admin: boolean;
}

export interface UserSummary {
  id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
  /** 소속 부서 — 그 사람이 구성원인 그룹 하나(D14). 없을 수 있다. */
  department?: { id: string; name: string } | null;
  /** 관리자 목록에만 온다 — 휴지통 포함 소유 문서 수(삭제 409와 같은 기준). */
  owned_documents?: number;
  /** 관리자 목록에만 온다 — 만든 폴더 수. */
  created_folders?: number;
  /** 관리자 목록에만 온다 — 이 사람이 관리하는 부서 이름(ADR-066). */
  managed_departments?: string[];
}

export interface GroupSummary {
  id: string;
  name: string;
  created_at: string;
  members: string[];
}

/** 부서 구성원. `primary`는 소속 부서가 이 부서인지다. */
export interface DepartmentMember {
  username: string;
  manager: boolean;
  primary: boolean;
}

/** 내가 보는 부서 한 곳 (`GET /api/departments`, ADR-066). */
export interface Department {
  id: string;
  name: string;
  description: string;
  folder_id: string | null;
  members: DepartmentMember[];
  granted_document_count: number;
  can_manage: boolean;
}

/** 휴지통의 문서 한 건. 영구 삭제 예정일은 서버가 보존 기간으로 계산한다 (ADR-060). */
export interface TrashItem {
  id: string;
  title: string;
  deleted_at: string;
  purge_at: string;
}

/** 감사 로그의 동작. DB 트리거·함수가 기록한다 (ADR-055). */
export type AuditAction =
  | "owner_changed"
  | "document_created"
  | "text_updated"
  | "document_deleted"
  | "document_trashed"
  | "document_restored"
  | "access_changed"
  | "folder_access_changed"
  | "group_member_changed"
  | "original_replaced"
  | "original_downloaded"
  | "original_previewed"
  | "share_changed"
  | "group_changed"
  | "user_changed"
  | "password_changed"
  | "folder_changed"
  | "token_changed";

export type AuditActorVia = "session" | "token" | "mcp" | "cli" | "share" | "worker";

export interface AuditEntry {
  id: number;
  occurred_at: string;
  action: AuditAction;
  actor: string | null;
  actor_via: AuditActorVia | null;
  db_role: string;
  document_id: string | null;
  /** 관리자에게도 제목만 보인다 — 본문·발췌는 없다 (ADR-055 결정 8). */
  document_title: string | null;
  detail: Record<string, unknown>;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

/** 문서 한 건의 활동 기록. 열람자는 변경, 소유자는 열람 기록까지 받는다 (ADR-055 결정 7). */
export interface DocumentActivityEntry {
  id: number;
  occurred_at: string;
  action: AuditAction;
  actor: string | null;
  actor_via: string | null;
  detail: Record<string, unknown>;
}

export interface DocumentActivityPage {
  items: DocumentActivityEntry[];
  next_before_id: number | null;
}

/** 열람 부여 대상으로 고를 수 있는 이름들. 로그인한 사용자 누구나 받는다 (ADR-044). */
export interface Principals {
  users: string[];
  groups: string[];
}

/** 문서의 열람 범위. 소유자만 읽고 바꾼다. public에는 부여 대상을 둘 수 없다. */
export interface DocumentAccess {
  follows_folder: boolean;
  folder: DocumentFolder | null;
  folder_scope: FolderScope | null;
  hidden_folder: boolean;
  visibility: Visibility;
  users: string[];
  groups: string[];
}

export interface DiagnosticDocument {
  document_id: string;
  title: string;
}

export interface DiagnosticDocumentList {
  count: number;
  items: DiagnosticDocument[];
}

export interface DuplicatePair {
  first: DiagnosticDocument;
  second: DiagnosticDocument;
  score: number | null;
}

export interface DuplicateList {
  count: number;
  items: DuplicatePair[];
}

export interface DiagnosticsResponse {
  orphans: DiagnosticDocumentList;
  duplicates: {
    identical: DuplicateList;
    overlaps: DuplicateList;
  };
  uncategorized: DiagnosticDocumentList;
  broken_links: {
    count: number;
    items: Array<{
      source: DiagnosticDocument;
      target_title: string;
    }>;
  };
}

export interface ClusterDocument {
  document_id: string;
  title: string;
}

export interface Cluster {
  name: string;
  size: number;
  documents: ClusterDocument[];
}

export interface ClusterConnection {
  source: string;
  target: string;
  count: number;
}

export interface ClustersResponse {
  clusters: Cluster[];
  connections: ClusterConnection[];
}

export const SUPPORTED_CONTENT_TYPES = [
  "pdf",
  "docx",
  "txt",
  "md",
  "hwp",
  "hwpx",
  "xlsx",
  "pptx",
  "png",
  "jpg",
  "jpeg",
] as const;

// 추출 중에는 서버가 텍스트를 바꾸는 요청을 409로 막는다 (ADR-052 결정 7). 화면은 미리 막고 이유를 말한다.
export const EXTRACTING_NOTICE =
  "원본에서 텍스트를 인식하는 중이라 텍스트 편집·되돌리기·다시 추출·원본 교체를 할 수 없습니다.";

// backend/openarchive/services/search.py의 MAX_K와 같아야 하며, 초과하면 API가 422를 반환한다.
export const MAX_K = 20;

export interface AdminShareSummary {
  id: string;
  name: string;
  owner: string;
  created_at: string;
  document_count: number;
  folder_count: number;
  tokens: Omit<TokenSummary, "scope">[];
}

export interface AuditCounts {
  total: number;
  actions: Partial<Record<AuditAction, number>>;
}

export interface AdminRevokedShareToken {
  id: number;
  revoked_at: string;
  share_name: string;
  token_name: string;
  revoked_by: string | null;
  actor_via: string | null;
}


export interface DbOutage {
  id: number;
  kind: "promotion" | "interruption";
  last_ok_at: string;
  first_failure_at: string;
  recovered_at: string;
  node_before: string | null;
  node_after: string | null;
  timeline_before: number | null;
  timeline_after: number | null;
  failed_checks: number;
  requests_retried: number;
  requests_unavailable: number;
  server_errors: number;
}
