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
  tags: string[];
  content_type: ContentType;
  chunk_index: number;
  content: string;
  score: number;
}

export interface SearchResponse {
  items: SearchResult[];
  sql: string;
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
  reconnect_events: null;
}

export const SUPPORTED_CONTENT_TYPES = ["pdf", "docx", "txt", "md"] as const;

// backend/app/services/search.py의 MAX_K와 같아야 하며, 초과하면 API가 422를 반환한다.
export const MAX_K = 20;
