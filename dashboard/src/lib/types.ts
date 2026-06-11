// TypeScript types for the ContextSeek HTTP API (served at the root path).
// Field shapes mirror contextseek.http.server Pydantic models / serializers.

export type Stage = "raw" | "extracted" | "knowledge" | "skill";
export type Stability = "ephemeral" | "transient" | "stable" | "permanent";
export type Layer = "summary" | "full";
export type LinkType =
  | "derived_from"
  | "supported_by"
  | "refuted_by"
  | "supersedes"
  | "merged_from"
  | "distilled_into"
  | "related_to"
  | "requires"
  | "synthesized_from";

export const STAGES: Stage[] = ["raw", "extracted", "knowledge", "skill"];

export interface Provenance {
  source_type: string;
  source_id: string;
  confidence: number;
  verified: boolean;
  created_by?: string;
  context?: unknown;
}

export interface ItemLink {
  target_id: string;
  relation: LinkType;
  strength: number;
  created_at: string;
}

export interface ContextItem {
  id: string;
  scope: string;
  content: unknown;
  stage: Stage;
  stability: Stability;
  hash: string;
  searchable: boolean;
  relevance_boost: number;
  importance: number;
  access_count: number;
  created_at: string;
  provenance: Provenance;
  tags: string[];
  // optional fields (present only when non-null/non-empty)
  abstract?: string;
  summary?: string;
  embedding?: number[];
  links?: ItemLink[];
  updated_at?: string;
  last_accessed_at?: string;
  superseded_by?: string;
  effective_confidence?: number;
  deleted_at?: string;
  deleted_reason?: string;
}

export interface SearchHit {
  id: string;
  score: number;
  layer: Layer;
  summary: string;
  content: unknown;
  tags: string[];
  provenance_summary: string;
  stage_confidence: number;
  recall_path: string;
}

export interface RetrieveMeta {
  layer: string;
  full_via: string;
  hint: string;
}

export interface RetrieveResponse {
  items: SearchHit[];
  _meta: RetrieveMeta;
}

export interface AddResponse {
  id: string;
  stage: Stage;
}

export interface StatusIdResponse {
  status: string;
  id: string;
}

export interface SeedResponse {
  status: string;
  seeded: number;
}

export interface ItemsResponse {
  items: ContextItem[];
}

export type ExpandResponse = ItemsResponse;
export type UpstreamResponse = ItemsResponse;

export interface CompactResponse {
  merged: number;
  archived: number;
  evolved: number;
}

export interface DreamResponse {
  total_dream_items: number;
  consolidation_patterns: number;
  consolidation_items: number;
  divergence_items: number;
}

export interface Overview {
  total_items: number;
  stage_distribution: Record<string, number>;
  pending_extraction: number;
  pending_convergence: number;
  distill_candidates: number;
}

export interface Health {
  status: string;
  version: string;
}

export interface EvidenceNode {
  item_id: string;
  intrinsic_confidence: number;
  effective_confidence: number;
  stage: Stage;
  depth: number;
  is_root: boolean;
  is_missing: boolean;
}

export interface EvidenceEdge {
  source_id: string;
  target_id: string;
  relation: LinkType;
  strength: number;
  contribution: number;
}

export interface EvidenceConflict {
  item_id: string;
  refuter_id: string;
  refutation_strength: number;
  net_confidence_impact: number;
}

export interface EvidenceChain {
  root_item_id: string;
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
  overall_confidence: number;
  max_depth: number;
  total_sources: number;
  critical_path: string[];
  critical_path_confidence: number;
  conflicts: EvidenceConflict[];
  has_conflicts: boolean;
  broken_links: string[];
  needs_reverification: boolean;
}

export interface GlobalOverview {
  total_items: number;
  health_score: number;
  active_scopes: number;
  stage_distribution: Record<string, number>;
  scope_top: { label: string; value: number }[];
  trend: { labels: string[]; values: number[] };
  heatmap: { stages: string[]; matrix: number[][] } | null;
  /** Scope name with highest refuted_by link count, null when none exist */
  risk_conflict_subject: string | null;
  /** Ratio of items with no incoming or outgoing links (0–1) */
  risk_orphan_ratio: number;
  /** Whether the extracted backlog is large enough to warrant compaction */
  risk_suggest_compact: boolean;
}

export interface WatchPath {
  path: string;
  scope: string;
}

export interface Config {
  storage_backend: string;
  llm_provider?: string;
  llm_model: string;
  llm_base_url?: string;
  llm_api_key?: string;
  embedding_provider?: string;
  embedding_model: string;
  embedding_base_url?: string;
  embedding_api_key?: string;
  default_scope: string;
  version: string;
  auto_sync: boolean;
  lifecycle_interval_seconds: number;
  watch_paths: WatchPath[];
  // OceanBase (storage_backend === "oceanbase")
  ob_host?: string;
  ob_port?: string;
  ob_db_name?: string;
  ob_table_name?: string;
  // SeekDB (storage_backend === "seekdb")
  seekdb_mode?: "embedded" | "server";
  seekdb_path?: string;
  seekdb_host?: string;
  seekdb_port?: string;
  seekdb_database?: string;
  // SQLite (storage_backend === "sqlite")
  sqlite_path?: string;
  // File (storage_backend === "file")
  storage_path?: string;
}

export interface ConfigUpdateRequest {
  storage_backend?: string;
  llm_provider?: string;
  llm_model?: string;
  llm_base_url?: string;
  llm_api_key?: string;
  embedding_provider?: string;
  embedding_model?: string;
  embedding_base_url?: string;
  embedding_api_key?: string;
  ob_host?: string;
  ob_port?: string;
  ob_db_name?: string;
  ob_table_name?: string;
  seekdb_host?: string;
  seekdb_port?: string;
  seekdb_database?: string;
  seekdb_path?: string;
  sqlite_path?: string;
  storage_path?: string;
}

// ---- request payloads ----

export interface AddRequest {
  scope: string;
  content: unknown;
  source?: string;
  tags?: string[];
}

export interface RetrieveRequest {
  scope: string;
  query: string;
  k?: number;
  full?: boolean;
  filters?: Record<string, unknown> | null;
  include_deleted?: boolean;
}

export interface ExpandRequest {
  scope: string;
  ids: string[];
}

export interface ForgetRequest {
  scope: string;
  item_id: string;
  reason?: string;
}

export interface DeleteRequest {
  scope: string;
  item_id: string;
  reason?: string;
  propagate?: boolean;
}

export interface FeedbackRequest {
  scope: string;
  item_id: string;
  score: number;
  reason?: string;
}

export interface CompactRequest {
  scope: string;
  dry_run?: boolean;
}

export interface DreamRequest {
  scope: string;
  dry_run?: boolean;
}

export interface UpstreamRequest {
  scope: string;
  item_id: string;
}

export interface EvidenceChainRequest {
  scope: string;
  item_id: string;
  max_depth?: number;
}

export interface ItemsRequest {
  scope: string;
  stage?: Stage;
}

export interface SkillToolsRequest {
  scope: string;
  fmt?: "openai" | "anthropic" | "mcp";
  query?: string;
  k?: number;
}

export interface SkillToolsResponse {
  tools: unknown[];
}

export interface SkillContextRequest {
  scope: string;
  query?: string;
  k?: number;
}

export interface SkillContextResponse {
  context: string;
}

export interface SkillMdItem {
  name: string;
  content: string;
}

export interface SkillMdRequest {
  scope: string;
}

export interface SkillMdResponse {
  skills: SkillMdItem[];
}
