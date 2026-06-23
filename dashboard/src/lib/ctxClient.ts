// Typed fetch wrapper for the ContextSeek HTTP API.
// Routes are served at the root path by contextseek.http.server, so BASE is ""
// by default (dev proxy and single-process prod both serve /add, /retrieve, ...).

import type {
  AddRequest,
  AddResponse,
  CompactRequest,
  CompactResponse,
  Config,
  ConfigTestRequest,
  ConfigTestResponse,
  ConfigUpdateRequest,
  DeleteRequest,
  DreamRequest,
  DreamResponse,
  EvidenceChain,
  EvidenceChainRequest,
  ExpandRequest,
  ExpandResponse,
  FeedbackRequest,
  ForgetRequest,
  GlobalOverview,
  Health,
  ItemsRequest,
  ItemsResponse,
  Overview,
  PlugCatalogResponse,
  PlugInstallJobResponse,
  PlugInstallRequest,
  PlugJobResponse,
  PlugStatusRefreshJobResponse,
  PlugStatusResponse,
  RetrieveRequest,
  RetrieveResponse,
  SeedResponse,
  SkillContextRequest,
  SkillContextResponse,
  SkillMdRequest,
  SkillMdResponse,
  SkillToolsRequest,
  SkillToolsResponse,
  StatusIdResponse,
  UpstreamRequest,
  UpstreamResponse,
} from "./types";

// Backend API base URL. Defaults to "" (relative) so the SPA calls /add,
// /retrieve, ... on its own origin — the single-process / desktop model where
// FastAPI serves both the API and this SPA (see contextseek.http.server). For
// separate-process development (front-end on a different port/host), set
// VITE_CTX_BASE to the absolute backend URL at build time.
const BASE = import.meta.env.VITE_CTX_BASE ?? "";
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_CTX_TIMEOUT_MS ?? 30_000);

type TauriInvoke = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

function getTauriInvoke(): TauriInvoke | undefined {
  const maybeWindow = window as Window & {
    __TAURI__?: { core?: { invoke?: TauriInvoke } };
  };
  return maybeWindow.__TAURI__?.core?.invoke;
}

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

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function get<T>(path: string, query?: Record<string, string>): Promise<T> {
  const qs = query ? `?${new URLSearchParams(query).toString()}` : "";
  const res = await fetchWithTimeout(`${BASE}${path}${qs}`);
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function put<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function getText(path: string): Promise<string> {
  const res = await fetchWithTimeout(`${BASE}${path}`);
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
  globalOverview: (scope?: string) =>
    get<GlobalOverview>("/global_overview", scope ? { scope } : undefined),
  scopes: () => get<{ scopes: string[] }>("/scopes"),
  config: () => get<Config>("/config"),
  plugs: () => get<PlugCatalogResponse>("/plugs"),
  plugStatus: (plug: string) => get<PlugStatusResponse>(`/plugs/${plug}`),
  plugJob: (jobId: string) => get<PlugJobResponse>(`/plugs/jobs/${jobId}`),
  plugStatusRefresh: (plug: string) =>
    post<PlugStatusRefreshJobResponse>(`/plugs/${plug}/status/refresh`, {}),
  plugInstall: (plug: string, req: PlugInstallRequest) =>
    post<PlugInstallJobResponse>(`/plugs/${plug}/install`, req),
  updateConfig: (req: ConfigUpdateRequest) =>
    put<{ status: string; restart_required: boolean }>("/config", req),
  testConfig: (req: ConfigTestRequest) => post<ConfigTestResponse>("/config/test", req),
  restart: async () => {
    const invoke = getTauriInvoke();
    if (invoke) {
      await invoke<void>("restart_service");
      return { status: "restarting" };
    }
    return post<{ status: string }>("/restart", {});
  },
  installPackage: (pkg: string) =>
    post<{ status: string; stdout: string; stderr: string; returncode: number }>(
      "/install",
      { package: pkg },
    ),
  seed: () => post<SeedResponse>("/seed", {}),
  health: () => get<Health>("/health"),
  metrics: () => getText("/metrics"),
  skillTools: (req: SkillToolsRequest) => post<SkillToolsResponse>("/skill_tools", req),
  skillContext: (req: SkillContextRequest) => post<SkillContextResponse>("/skill_context", req),
  skillMd: (req: SkillMdRequest) => post<SkillMdResponse>("/skill_md", req),
};
