export type EmbeddingStatus = "pending" | "processing" | "ready" | "error";
export type ContentType = "pdf" | "docx" | "txt" | "md";
export type Visibility = "public" | "private";

export interface DocumentSummary {
  id: string;
  title: string;
  filename: string | null;
  content_type: ContentType;
  version: number;
  owner_id: string;
  visibility: Visibility;
  tags: string[];
  embedding_status: EmbeddingStatus;
  created_at: string;
  updated_at: string;
}

export interface TextVersion {
  version: number;
  created_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  content: string;
  versions: TextVersion[];
  chunk_count: number;
  chunk_version: number | null;
}

export interface SearchResult {
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
}

export interface SearchVia {
  from_document_id: string;
  kind: string;
  depth: number;
}

export interface SearchResponse {
  items: SearchResult[];
  sql: string;
}

export interface RelatedDocument {
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

export interface JobCounts {
  pending: number;
  processing: number;
  error: number;
}

export interface SystemStatus {
  node_address: string | null;
  node_port: number;
  jobs: JobCounts;
  inconsistent_documents: number;
  embedding_provider: string;
}

export interface AuthStatus {
  authenticated: boolean;
  username: string | null;
  is_admin: boolean;
}

export interface UserSummary {
  id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
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

export const SUPPORTED_CONTENT_TYPES = ["pdf", "docx", "txt", "md"] as const;

// backend/app/services/search.py의 MAX_K와 같아야 하며, 초과하면 API가 422를 반환한다.
export const MAX_K = 20;
