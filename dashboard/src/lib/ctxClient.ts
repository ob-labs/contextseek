// Typed fetch wrapper for the ContextSeek HTTP API.
// Routes are served at the root path by contextseek.http.server, so BASE is ""
// by default (dev proxy and single-process prod both serve /add, /retrieve, ...).

import type {
  AddRequest,
  AddResponse,
  CompactRequest,
  CompactResponse,
  DeleteRequest,
  DreamRequest,
  DreamResponse,
  EvidenceChain,
  EvidenceChainRequest,
  ExpandRequest,
  ExpandResponse,
  FeedbackRequest,
  ForgetRequest,
  Health,
  ItemsRequest,
  ItemsResponse,
  Overview,
  RetrieveRequest,
  RetrieveResponse,
  SeedResponse,
  StatusIdResponse,
  UpstreamRequest,
  UpstreamResponse,
} from "./types";

// Backend API base URL, injected at build time via VITE_CTX_BASE. Defaults to
// the local backend on :8000. Front-end and backend run as separate processes
// (separate ports/hosts), so this is an absolute, cross-origin URL.
const BASE = import.meta.env.VITE_CTX_BASE ?? "http://127.0.0.1:8000";

export class CtxError extends Error {
  constructor(
    public status: number,
    public body: unknown,
  ) {
    super(`ContextSeek request failed (${status})`);
    this.name = "CtxError";
  }
}

async function parseError(res: Response): Promise<never> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = await res.text().catch(() => "");
  }
  throw new CtxError(res.status, body);
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function get<T>(path: string, query?: Record<string, string>): Promise<T> {
  const qs = query ? `?${new URLSearchParams(query).toString()}` : "";
  const res = await fetch(`${BASE}${path}${qs}`);
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function getText(path: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) return parseError(res);
  return res.text();
}

export const ctx = {
  add: (req: AddRequest) => post<AddResponse>("/add", req),
  retrieve: (req: RetrieveRequest) => post<RetrieveResponse>("/retrieve", req),
  expand: (req: ExpandRequest) => post<ExpandResponse>("/expand", req),
  forget: (req: ForgetRequest) => post<StatusIdResponse>("/forget", req),
  delete: (req: DeleteRequest) => post<StatusIdResponse>("/delete", req),
  feedback: (req: FeedbackRequest) => post<StatusIdResponse>("/feedback", req),
  compact: (req: CompactRequest) => post<CompactResponse>("/compact", req),
  dream: (req: DreamRequest) => post<DreamResponse>("/dream", req),
  upstream: (req: UpstreamRequest) => post<UpstreamResponse>("/upstream", req),
  evidenceChain: (req: EvidenceChainRequest) => post<EvidenceChain>("/evidence_chain", req),
  items: (req: ItemsRequest) => post<ItemsResponse>("/items", req),
  overview: (scope: string) => get<Overview>("/overview", { scope }),
  seed: () => post<SeedResponse>("/seed", {}),
  health: () => get<Health>("/health"),
  metrics: () => getText("/metrics"),
};
