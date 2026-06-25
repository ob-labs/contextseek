import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Database,
  PlugZap,
  RefreshCw,
  Route,
  Search,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { BarList } from "@/components/charts/BarList";
import { LineChart } from "@/components/charts/LineChart";
import { StatRows } from "@/components/charts/StatRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useScope } from "@/context/ScopeContext";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type {
  Config,
  ContextItem,
  PlugBlockerStage,
  PlugInstallJobResponse,
  PlugJobResponse,
  PlugJobPhase,
  PlugLinkerResult,
  PlugLinkerStatus,
  PlugStatusRefreshJobResponse,
} from "@/lib/types";

type EntryStatus =
  | "checking"
  | "ready"
  | "connected"
  | "partial"
  | "needs_action"
  | "disabled"
  | "planned";

interface PlugEntry {
  id: string;
  name: string;
  status: EntryStatus;
}

interface PlugDefinition {
  id: string;
  name: string;
  kindKey: string;
  icon: LucideIcon;
  status: EntryStatus;
  summaryKey: string;
  entries: PlugEntry[];
}

interface MockOperation {
  plug: string;
  entry: string;
  title: string;
  status: EntryStatus;
  steps: OperationStep[];
  progress?: OperationProgress;
}

interface OperationProgress {
  label?: string;
  current: number;
  total: number;
  detail: string;
  percent?: number;
}

type OperationStepState = "done" | "current" | "pending" | "blocked";

interface OperationStep {
  title: string;
  state: OperationStepState;
  detail?: string;
}

const PLUG_JOB_POLL_MS = 1_000;
const SHOW_RUNTIME_OVERVIEW = false;
const ENABLED_POWERMEM_ENTRY_IDS = new Set(["claude-code", "codex"]);

const SOURCE_COLORS: Record<string, string> = {
  document: "linear-gradient(90deg,#4b8dff,#6cb2ff)",
  trace_extraction: "linear-gradient(90deg,#6ed18f,#98e1af)",
  external_api: "linear-gradient(90deg,#f5b83d,#ffd27a)",
  agent_session: "linear-gradient(90deg,#b694ff,#d4c2ff)",
  retrieval: "linear-gradient(90deg,#ff8c6b,#ffb49e)",
  knowledge: "linear-gradient(90deg,#54c6c6,#8de8e8)",
  api: "linear-gradient(90deg,#4b8dff,#6cb2ff)",
};
const FALLBACK_COLOR = "linear-gradient(90deg,#94a3b8,#cbd5e1)";

const POWERMEM_ENTRIES: PlugEntry[] = [
  {
    id: "claude-code",
    name: "Claude Code",
    status: "ready",
  },
  {
    id: "codex",
    name: "Codex",
    status: "ready",
  },
  {
    id: "cursor",
    name: "Cursor",
    status: "planned",
  },
  {
    id: "vscode",
    name: "VS Code",
    status: "planned",
  },
  {
    id: "windsurf",
    name: "Windsurf",
    status: "planned",
  },
  {
    id: "github-copilot",
    name: "GitHub Copilot",
    status: "planned",
  },
  {
    id: "opencode",
    name: "OpenCode",
    status: "planned",
  },
  {
    id: "claude-desktop",
    name: "Claude Desktop",
    status: "planned",
  },
  {
    id: "cline",
    name: "Cline",
    status: "planned",
  },
  {
    id: "openclaw",
    name: "OpenClaw",
    status: "planned",
  },
  {
    id: "qoder",
    name: "Qoder",
    status: "planned",
  },
];

const PLUGS: PlugDefinition[] = [
  {
    id: "powermem",
    name: "PowerMem",
    kindKey: "ingress.plugKind.memory",
    icon: Database,
    status: "ready",
    summaryKey: "ingress.plug.powermem.summary",
    entries: POWERMEM_ENTRIES,
  },
  {
    id: "rag",
    name: "RAG",
    kindKey: "ingress.plugKind.retrieval",
    icon: Search,
    status: "planned",
    summaryKey: "ingress.plug.rag.summary",
    entries: [],
  },
  {
    id: "trace",
    name: "Trace",
    kindKey: "ingress.plugKind.runtime",
    icon: Route,
    status: "planned",
    summaryKey: "ingress.plug.trace.summary",
    entries: [],
  },
];

function SectionCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">{children}</CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: EntryStatus }) {
  const { t } = useI18n();
  const label = {
    ready: t("ingress.statusLabel.ready"),
    checking: t("ingress.statusLabel.checking"),
    connected: t("ingress.statusLabel.connected"),
    partial: t("ingress.statusLabel.connected"),
    needs_action: t("ingress.statusLabel.needsAction"),
    disabled: t("ingress.statusLabel.disabled"),
    planned: t("ingress.statusLabel.planned"),
  }[status];
  const className =
    status === "connected"
      ? "bg-emerald-600 text-white"
      : status === "checking"
        ? "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300"
      : status === "partial"
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : status === "needs_action"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : status === "planned"
          ? "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300"
          : "";
  const variant =
    status === "connected"
      ? "default"
      : status === "partial" ||
          status === "checking" ||
          status === "needs_action" ||
          status === "planned"
        ? "outline"
        : "secondary";
  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
}

function isTargetNotDetected(result?: PlugLinkerResult) {
  return result?.blocker_code === "target_not_detected";
}

function ActionIcon({
  status,
  result,
}: {
  status: EntryStatus;
  result?: PlugLinkerResult;
}) {
  if (status === "connected") return <RefreshCw className="h-3.5 w-3.5" />;
  if (status === "checking") return <RefreshCw className="h-3.5 w-3.5" />;
  if (status === "needs_action" && isTargetNotDetected(result)) {
    return <RefreshCw className="h-3.5 w-3.5" />;
  }
  if (status === "needs_action") return <Wrench className="h-3.5 w-3.5" />;
  if (status === "disabled" || status === "planned") {
    return <CircleAlert className="h-3.5 w-3.5" />;
  }
  return <PlugZap className="h-3.5 w-3.5" />;
}

function actionLabel(
  status: EntryStatus,
  t: (key: string, vars?: Record<string, string | number>) => string,
  result?: PlugLinkerResult,
) {
  if (status === "connected") return t("ingress.action.updateConfig");
  if (status === "checking") return t("ingress.action.checking");
  if (status === "needs_action" && isTargetNotDetected(result)) {
    return t("ingress.action.detect");
  }
  if (status === "needs_action") return t("ingress.action.fix");
  if (status === "disabled" || status === "planned") {
    return t("ingress.action.comingSoon");
  }
  return t("ingress.action.connect");
}

function isPowerMemEntryEnabled(entry: PlugEntry): boolean {
  return ENABLED_POWERMEM_ENTRY_IDS.has(entry.id);
}

function StepIcon({ state }: { state: OperationStepState }) {
  if (state === "done") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />;
  }
  if (state === "blocked") {
    return <CircleAlert className="h-3.5 w-3.5 text-amber-600" />;
  }
  return <CircleDot className="h-3.5 w-3.5 text-muted-foreground" />;
}

function StepStateBadge({ state }: { state: OperationStepState }) {
  const { t } = useI18n();
  const label = {
    done: t("ingress.stepState.done"),
    current: t("ingress.stepState.current"),
    pending: t("ingress.stepState.pending"),
    blocked: t("ingress.stepState.blocked"),
  }[state];
  const className =
    state === "done"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : state === "blocked"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : state === "current"
          ? "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300"
          : "";
  return (
    <Badge variant={state === "pending" ? "secondary" : "outline"} className={className}>
      {label}
    </Badge>
  );
}

function OperationFlow({ steps }: { steps: OperationStep[] }) {
  const { t } = useI18n();
  const blockedStep = steps.find((step) => step.state === "blocked");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {steps.map((step, index) => (
          <div key={`${step.title}-${index}`} className="flex items-center gap-2">
            <div
              className={cn(
                "flex items-center gap-2 rounded-full border px-2.5 py-1.5",
                step.state === "blocked" &&
                  "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200",
                step.state === "done" &&
                  "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
              )}
            >
              <StepIcon state={step.state} />
              <span className="whitespace-nowrap font-medium">{step.title}</span>
              <StepStateBadge state={step.state} />
            </div>
            {index < steps.length - 1 && (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            )}
          </div>
        ))}
      </div>
      {blockedStep ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          <span className="font-medium">{t("ingress.flow.currentBlocker")}</span>
          {blockedStep.detail ?? blockedStep.title}
        </div>
      ) : (
        <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
          {t("ingress.flow.detectionScope")}
        </div>
      )}
    </div>
  );
}

function buildFlowSteps({
  entry,
  blockedAt,
  blockedDetail,
  complete,
  t,
}: {
  entry: PlugEntry;
  blockedAt?: "target" | "runtime" | "channel" | "config";
  blockedDetail?: string;
  complete?: boolean;
  t: (key: string, vars?: Record<string, string | number>) => string;
}): OperationStep[] {
  const stages: Array<{
    id: "target" | "runtime" | "channel" | "config";
    title: string;
  }> = [
    { id: "target", title: t("ingress.flow.target", { name: entry.name }) },
    { id: "runtime", title: t("ingress.flow.runtime") },
    { id: "channel", title: t("ingress.flow.channel") },
    { id: "config", title: t("ingress.flow.config") },
  ];
  const blockedIndex = blockedAt
    ? stages.findIndex((stage) => stage.id === blockedAt)
    : -1;
  return stages.map((stage, index) => {
    if (complete) {
      return { ...stage, state: "done" };
    }
    if (index === blockedIndex) {
      return { ...stage, state: "blocked", detail: blockedDetail };
    }
    return {
      ...stage,
      state: blockedIndex >= 0 && index > blockedIndex ? "pending" : "done",
    };
  });
}

function buildFlowStepsForPhase(
  entry: PlugEntry,
  phase: PlugJobPhase,
  t: (key: string, vars?: Record<string, string | number>) => string,
): OperationStep[] {
  const stages: Array<{
    id: "target" | "runtime" | "channel" | "config";
    title: string;
  }> = [
    { id: "target", title: t("ingress.flow.target", { name: entry.name }) },
    { id: "runtime", title: t("ingress.flow.runtime") },
    { id: "channel", title: t("ingress.flow.channel") },
    { id: "config", title: t("ingress.flow.config") },
  ];
  if (phase === "done") {
    return stages.map((stage) => ({ ...stage, state: "done" }));
  }
  const currentIndex = Math.max(
    0,
    stages.findIndex((stage) => stage.id === phase),
  );
  return stages.map((stage, index) => {
    if (index < currentIndex) {
      return { ...stage, state: "done" };
    }
    if (index === currentIndex) {
      return { ...stage, state: "current" };
    }
    return { ...stage, state: "pending" };
  });
}

function entryRequirement(
  entry: PlugEntry,
  t: (key: string, vars?: Record<string, string | number>) => string,
  result?: PlugLinkerResult,
): string | undefined {
  if (result?.status === "needs_action") {
    return blockerDetail(entry, result, t);
  }
  return undefined;
}

function toEntryStatus(status: PlugLinkerStatus): EntryStatus {
  if (status === "checking") return "checking";
  if (status === "ready") return "ready";
  if (status === "connected") return "connected";
  if (status === "needs_action") return "needs_action";
  if (status === "disabled") return "disabled";
  return "planned";
}

function toBlockerStage(stage?: PlugBlockerStage | null) {
  if (stage === "target" || stage === "runtime" || stage === "channel") {
    return stage;
  }
  return "config";
}

function blockerDetail(
  entry: PlugEntry,
  result: PlugLinkerResult,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (result.blocker_code === "target_not_detected") {
    return t("ingress.detect.notDetected", { name: entry.name });
  }
  if (result.blocker_code === "powermem_install_failed") {
    return t("ingress.blocker.powermemInstallFailed");
  }
  if (result.blocker_code === "powermem_runtime_failed") {
    return t("ingress.blocker.powermemRuntimeFailed");
  }
  if (result.blocker_code === "powermem_runtime_missing") {
    return t("ingress.blocker.powermemRuntimeMissing");
  }
  if (result.blocker_code === "powermem_env_missing") {
    return t("ingress.blocker.powermemEnvMissing");
  }
  if (result.blocker_code === "agent_plugin_failed") {
    return t("ingress.blocker.agentPluginFailed", { name: entry.name });
  }
  if (result.blocker_code === "config_invalid") {
    return t("ingress.blocker.configInvalid", { name: entry.name });
  }
  return t("ingress.blocker.needsAction", { name: entry.name });
}

function buildFlowStepsFromResult(
  entry: PlugEntry,
  result: PlugLinkerResult,
  t: (key: string, vars?: Record<string, string | number>) => string,
): OperationStep[] {
  if (result.status !== "needs_action") {
    return buildFlowSteps({ entry, complete: true, t });
  }
  return buildFlowSteps({
    entry,
    blockedAt: toBlockerStage(result.blocker_stage),
    blockedDetail: blockerDetail(entry, result, t),
    t,
  });
}

function buildFlowStepsFromJob(
  entry: PlugEntry,
  job: PlugInstallJobResponse,
  t: (key: string, vars?: Record<string, string | number>) => string,
): OperationStep[] {
  if (job.result) {
    return buildFlowStepsFromResult(entry, job.result, t);
  }
  return buildFlowStepsForPhase(entry, job.phase, t);
}

function operationTitleFromResult(
  result: PlugLinkerResult,
  {
    updateConfig,
    t,
  }: {
    updateConfig: boolean;
    t: (key: string, vars?: Record<string, string | number>) => string;
  },
): string {
  if (result.status === "needs_action") {
    return t("ingress.operation.needsAction");
  }
  if (updateConfig) {
    return t("ingress.operation.configUpdated");
  }
  return t("ingress.operation.connectDone");
}

function operationTitleFromJob(
  job: PlugInstallJobResponse,
  {
    updateConfig,
    t,
  }: {
    updateConfig: boolean;
    t: (key: string, vars?: Record<string, string | number>) => string;
  },
): string {
  if (job.status === "queued" || job.status === "running") {
    return t("ingress.operation.installRunning");
  }
  if (job.result) {
    return operationTitleFromResult(job.result, { updateConfig, t });
  }
  return t("ingress.operation.needsAction");
}

function isTerminalPlugJob(job: PlugJobResponse): boolean {
  return job.status === "succeeded" || job.status === "failed";
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = index === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[index]}`;
}

function jobProgressDetail(
  job: PlugInstallJobResponse,
  t: (key: string, vars?: Record<string, string | number>) => string,
): OperationProgress | undefined {
  const label = job.progress_label ?? undefined;
  const current = Math.max(job.progress_current ?? 0, 0);
  const total = Math.max(job.progress_total ?? 0, 0);
  if (!label && current <= 0 && total <= 0) {
    return undefined;
  }
  const percent = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : undefined;
  const detail =
    total > 0
      ? t("ingress.progress.download", {
          name: label ?? t("ingress.progress.runtime"),
          current: formatBytes(current),
          total: formatBytes(total),
        })
      : t("ingress.progress.downloadUnknownTotal", {
          name: label ?? t("ingress.progress.runtime"),
          current: formatBytes(current),
        });
  return {
    label,
    current,
    total,
    detail,
    percent,
  };
}

function buildThroughput(items: ContextItem[]) {
  const today = new Date();
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() - (6 - i));
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return { label: `${mm}/${dd}`, date: d.toISOString().slice(0, 10) };
  });

  const counts: Record<string, number> = {};
  for (const item of items) {
    const date = item.created_at.slice(0, 10);
    counts[date] = (counts[date] ?? 0) + 1;
  }

  return {
    labels: days.map((d) => d.label),
    values: days.map((d) => counts[d.date] ?? 0),
  };
}

function buildContribution(items: ContextItem[]) {
  const counts: Record<string, number> = {};
  for (const item of items) {
    const st = item.provenance?.source_type || "unknown";
    counts[st] = (counts[st] ?? 0) + 1;
  }
  return Object.entries(counts)
    .sort(([, a], [, b]) => b - a)
    .map(([label, value]) => ({
      label,
      value,
      color: SOURCE_COLORS[label] ?? FALLBACK_COLOR,
    }));
}

function toHHMM(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function buildEvents(items: ContextItem[]) {
  const sorted = [...items].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
  return sorted.slice(0, 6).map((item) => {
    const st = item.provenance?.source_type || "api";
    const raw =
      typeof item.content === "string"
        ? item.content
        : JSON.stringify(item.content);
    const preview = raw.length > 40 ? `${raw.slice(0, 40)}...` : raw;
    return { label: toHHMM(item.created_at), value: `[${st}] ${preview}` };
  });
}

export function IngressPanel() {
  const { t } = useI18n();
  const { scope } = useScope();

  const [config, setConfig] = useState<Config | null>(null);
  const [items, setItems] = useState<ContextItem[]>([]);
  const [selectedPlugId, setSelectedPlugId] = useState("powermem");
  const [entryResults, setEntryResults] = useState<Record<string, PlugLinkerResult>>(
    {},
  );
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusRefreshJob, setStatusRefreshJob] =
    useState<PlugStatusRefreshJobResponse | null>(null);
  const [installingEntryId, setInstallingEntryId] = useState<string | null>(null);
  const [operation, setOperation] = useState<MockOperation | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const c = await ctx.config();
      setConfig(c);
    } catch {
      // silently ignore
    }
  }, []);

  const fetchItems = useCallback(async () => {
    try {
      const r = await ctx.items({ scope });
      setItems(r.items);
    } catch {
      setItems([]);
    }
  }, [scope]);

  const mergePlugResults = useCallback((plugId: string, results: PlugLinkerResult[]) => {
    setEntryResults((prev) => {
      const next = { ...prev };
      for (const result of results) {
        next[`${plugId}:${result.linker}`] = result;
      }
      return next;
    });
  }, []);

  const fetchPlugStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const status = await ctx.plugStatus("powermem");
      mergePlugResults(status.id, status.entries);
      if (status._meta?.refresh_job_id && status._meta.refreshing) {
        setStatusRefreshJob((prev) =>
          prev?.job_id === status._meta?.refresh_job_id ? prev : null,
        );
      }
    } catch {
      // Keep the static preview state when the backend is unavailable.
    } finally {
      setStatusLoading(false);
    }
  }, [mergePlugResults]);

  const mergeRefreshJob = useCallback(
    (job: PlugStatusRefreshJobResponse) => {
      setStatusRefreshJob(job);
      if (job.entries.length > 0) {
        mergePlugResults(job.plug, job.entries);
      }
      if (job.result?.entries.length) {
        mergePlugResults(job.result.id, job.result.entries);
      }
    },
    [mergePlugResults],
  );

  const refreshPlugStatus = useCallback(async () => {
    try {
      let job = await ctx.plugStatusRefresh("powermem");
      mergeRefreshJob(job);
      while (!isTerminalPlugJob(job)) {
        await delay(PLUG_JOB_POLL_MS);
        const next = await ctx.plugJob(job.job_id);
        if (next.kind !== "status_refresh") {
          return;
        }
        job = next as PlugStatusRefreshJobResponse;
        mergeRefreshJob(job);
      }
    } catch {
      setStatusRefreshJob(null);
    }
  }, [mergeRefreshJob]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  useEffect(() => {
    fetchPlugStatus();
  }, [fetchPlugStatus]);

  useEffect(() => {
    refreshPlugStatus();
  }, [refreshPlugStatus]);

  const throughput = useMemo(() => buildThroughput(items), [items]);
  const contribution = useMemo(() => buildContribution(items), [items]);
  const events = useMemo(() => buildEvents(items), [items]);
  const latestItem = useMemo(
    () =>
      items.length
        ? [...items].sort(
            (a, b) =>
              new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          )[0]
        : null,
    [items],
  );

  const selectedPlug =
    PLUGS.find((plug) => plug.id === selectedPlugId) ?? PLUGS[0];
  const statusRefreshing =
    statusRefreshJob !== null && !isTerminalPlugJob(statusRefreshJob);
  const statusRefreshProgress =
    statusRefreshJob && statusRefreshJob.progress_total > 0
      ? `${statusRefreshJob.progress_current}/${statusRefreshJob.progress_total}`
      : "";

  const entryStatus = useCallback(
    (plug: PlugDefinition, entry: PlugEntry) => {
      if (plug.id === "powermem" && !isPowerMemEntryEnabled(entry)) {
        return "planned";
      }
      const result = entryResults[`${plug.id}:${entry.id}`];
      if (result?.status === "checking" && !statusRefreshing) {
        return entry.status;
      }
      return result ? toEntryStatus(result.status) : entry.status;
    },
    [entryResults, statusRefreshing],
  );

  const plugDisplayStatus = useCallback(
    (plug: PlugDefinition): EntryStatus => {
      if (plug.status === "planned" || plug.entries.length === 0) {
        return plug.status;
      }
      const statuses = plug.entries.map((entry) => entryStatus(plug, entry));
      const availableStatuses = statuses.filter((status) => status !== "disabled");
      const connectedCount = availableStatuses.filter(
        (status) => status === "connected",
      ).length;
      if (availableStatuses.length > 0 && connectedCount === availableStatuses.length) {
        return "connected";
      }
      if (connectedCount > 0) {
        return "partial";
      }
      if (availableStatuses.some((status) => status === "checking")) {
        return "checking";
      }
      return plug.status;
    },
    [entryStatus],
  );

  const plugStatus = plugDisplayStatus(selectedPlug);

  const overviewRows = [
    { label: t("ingress.status.totalItems"), value: String(items.length) },
    {
      label: t("ingress.status.watchPaths"),
      value: config ? String(config.watch_paths.length) : "-",
    },
    {
      label: t("ingress.status.latestWrite"),
      value: latestItem ? toHHMM(latestItem.created_at) : "-",
    },
    {
      label: t("ingress.status.autoSync"),
      value:
        config == null ? (
          "-"
        ) : (
          <Badge variant={config.auto_sync ? "secondary" : "outline"}>
            {config.auto_sync ? "on" : "off"}
          </Badge>
        ),
    },
  ];

  const settingsRows = [
    {
      label: t("ingress.config.defaultScope"),
      value: config?.default_scope ?? "-",
    },
    {
      label: t("ingress.config.lifecycle"),
      value:
        config?.lifecycle_interval_seconds != null
          ? `${config.lifecycle_interval_seconds}s`
          : "-",
    },
    {
      label: t("ingress.config.autoSync"),
      value:
        config == null ? (
          "-"
        ) : (
          <Badge variant={config.auto_sync ? "secondary" : "outline"}>
            {config.auto_sync ? "on" : "off"}
          </Badge>
        ),
    },
  ];

  const handleEntryAction = async (entry: PlugEntry) => {
    const status = entryStatus(selectedPlug, entry);
    const result = entryResults[`${selectedPlug.id}:${entry.id}`];
    const requirement = entryRequirement(entry, t, result);
    if (status === "planned") {
      setOperation({
        plug: selectedPlug.name,
        entry: entry.name,
        title: t("ingress.operation.reserved"),
        status,
        steps: buildFlowSteps({
          entry,
          blockedAt: "runtime",
          blockedDetail: t("ingress.blocker.noBackendRuntime"),
          t,
        }),
      });
      return;
    }

    if (status === "disabled") {
      setOperation({
        plug: selectedPlug.name,
        entry: entry.name,
        title: t("ingress.operation.unavailable"),
        status,
        steps: buildFlowSteps({
          entry,
          blockedAt: "channel",
          blockedDetail: requirement ?? t("ingress.blocker.channelUnavailable"),
          t,
        }),
      });
      return;
    }

    if (selectedPlug.id !== "powermem") {
      return;
    }

    const targetOnly = status === "needs_action" && isTargetNotDetected(result);
    const updateConfig = status === "connected";
    setInstallingEntryId(entry.id);
    try {
      if (targetOnly) {
        setOperation(null);
        mergePlugResults(selectedPlug.id, [
          {
            linker: entry.id,
            status: "checking",
            changed: false,
            dry_run: true,
            actions: [],
            warnings: [],
            blocker_stage: null,
            blocker_code: null,
          },
        ]);
        let job = await ctx.plugInstall(selectedPlug.id, {
          linker: entry.id,
          target_only: true,
        });
        if (job.result) {
          mergePlugResults(selectedPlug.id, [job.result]);
        }
        while (!isTerminalPlugJob(job)) {
          await delay(PLUG_JOB_POLL_MS);
          const next = await ctx.plugJob(job.job_id);
          if (next.kind !== "install") {
            return;
          }
          job = next as PlugInstallJobResponse;
          if (job.result) {
            mergePlugResults(selectedPlug.id, [job.result]);
          }
        }
        return;
      }

      const updateOperationFromJob = (job: PlugInstallJobResponse) => {
        if (job.result) {
          mergePlugResults(selectedPlug.id, [job.result]);
        }
        setOperation({
          plug: selectedPlug.name,
          entry: entry.name,
          title: operationTitleFromJob(job, { updateConfig, t }),
          status: job.result ? toEntryStatus(job.result.status) : status,
          steps: buildFlowStepsFromJob(entry, job, t),
          progress: jobProgressDetail(job, t),
        });
      };

      let job = await ctx.plugInstall(selectedPlug.id, {
        linker: entry.id,
      });
      updateOperationFromJob(job);
      while (!isTerminalPlugJob(job)) {
        await delay(PLUG_JOB_POLL_MS);
        const next = await ctx.plugJob(job.job_id);
        if (next.kind !== "install") {
          return;
        }
        job = next as PlugInstallJobResponse;
        updateOperationFromJob(job);
      }
    } catch {
      if (targetOnly) {
        if (result) {
          mergePlugResults(selectedPlug.id, [result]);
        }
        return;
      }
      const failedResult: PlugLinkerResult = {
        linker: entry.id,
        status: "needs_action",
        changed: false,
        dry_run: false,
        actions: [],
        warnings: [],
        blocker_stage: "config",
        blocker_code: "request_failed",
      };
      mergePlugResults(selectedPlug.id, [failedResult]);
      setOperation({
        plug: selectedPlug.name,
        entry: entry.name,
        title: t("ingress.operation.needsAction"),
        status: "needs_action",
        steps: buildFlowSteps({
          entry,
          blockedAt: "config",
          blockedDetail: t("ingress.blocker.requestFailed"),
          t,
        }),
      });
    } finally {
      setInstallingEntryId(null);
    }
  };

  return (
    <div className="space-y-5 p-6">
      <div className="grid gap-4 xl:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="rounded-md border bg-card">
          <div className="border-b px-4 py-3">
            <div className="text-sm font-semibold">{t("ingress.plugCatalog")}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("ingress.plugCatalog.summary", {
                plugs: PLUGS.length,
                entries: POWERMEM_ENTRIES.length,
              })}
            </div>
          </div>
          <div className="p-2">
            {PLUGS.map((plug) => {
              const Icon = plug.icon;
              const active = plug.id === selectedPlug.id;
              return (
                <button
                  key={plug.id}
                  type="button"
                  onClick={() => {
                    setSelectedPlugId(plug.id);
                    setOperation(null);
                  }}
                  className={cn(
                    "mb-1 flex w-full items-start gap-3 rounded-md px-3 py-2 text-left transition-colors",
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "hover:bg-muted",
                  )}
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{plug.name}</span>
                      <StatusBadge status={plugDisplayStatus(plug)} />
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">
                      {t(plug.kindKey)} ·{" "}
                      {t("ingress.entryCount", { count: plug.entries.length })}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </aside>

        <section className="min-w-0 rounded-md border bg-card">
          <div className="flex flex-col gap-3 border-b px-4 py-3 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <selectedPlug.icon className="h-4 w-4 text-muted-foreground" />
                <h1 className="text-base font-semibold">{selectedPlug.name}</h1>
                <Badge variant="outline">{t(selectedPlug.kindKey)}</Badge>
                <StatusBadge status={plugStatus} />
              </div>
              <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
                {t(selectedPlug.summaryKey)}
              </p>
            </div>
            <Badge variant="secondary">{t("ingress.previewOnly")}</Badge>
          </div>

          <div className="space-y-3 p-4">
            {operation ? (
              <div className="rounded-md border bg-muted/20 p-3 text-xs">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium">
                      {operation.plug} / {operation.entry}
                    </div>
                    <div className="mt-0.5 text-muted-foreground">
                      {operation.title}
                    </div>
                  </div>
                  <StatusBadge status={operation.status} />
                </div>
                <OperationFlow steps={operation.steps} />
                {operation.progress && (
                  <div className="mt-3 rounded-md border bg-background px-3 py-2">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span
                        className="min-w-0 truncate text-muted-foreground"
                        title={operation.progress.detail}
                      >
                        {operation.progress.detail}
                      </span>
                      {operation.progress.percent != null && (
                        <span className="shrink-0 font-medium">
                          {operation.progress.percent}%
                        </span>
                      )}
                    </div>
                    {operation.progress.percent != null && (
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-blue-600 transition-all"
                          style={{ width: `${operation.progress.percent}%` }}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : statusRefreshing ? (
              <div className="rounded-md border border-blue-500/30 bg-blue-500/5 p-3 text-xs text-blue-900 dark:text-blue-100">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    <span className="font-medium">
                      {t("ingress.statusRefresh.title")}
                    </span>
                  </div>
                  {statusRefreshProgress && (
                    <Badge
                      variant="outline"
                      className="border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-200"
                    >
                      {statusRefreshProgress}
                    </Badge>
                  )}
                </div>
                <div className="mt-1 text-blue-800/80 dark:text-blue-100/80">
                  {t("ingress.statusRefresh.detail")}
                </div>
              </div>
            ) : (
              <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
                {statusLoading ? t("ingress.loadingStatus") : t("ingress.phaseTwoNote")}
              </div>
            )}

            {selectedPlug.entries.length === 0 ? (
              <div className="rounded-md border p-6 text-sm text-muted-foreground">
                {t("ingress.noEntries")}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {selectedPlug.entries.map((entry) => {
                  const status = entryStatus(selectedPlug, entry);
                  const result = entryResults[`${selectedPlug.id}:${entry.id}`];
                  const requirement = entryRequirement(entry, t, result);
                  const installing = installingEntryId === entry.id;
                  const enabled =
                    selectedPlug.id !== "powermem" || isPowerMemEntryEnabled(entry);
                  const detailText = enabled
                    ? requirement
                    : t("ingress.blocker.comingSoon");
                  return (
                    <div
                      key={entry.id}
                      className={cn(
                        "flex h-full flex-col rounded-md border p-3 transition-colors",
                        !enabled
                          ? "border-muted bg-muted/30 opacity-60"
                          : status === "connected"
                            ? "border-emerald-500/30 bg-emerald-500/5"
                            : "bg-background hover:bg-muted/30",
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <CircleDot className="h-3.5 w-3.5 text-muted-foreground" />
                            <span className="truncate text-sm font-medium">
                              {entry.name}
                            </span>
                          </div>
                          <div
                            className="mt-1 h-4 truncate text-xs text-amber-700 dark:text-amber-300"
                            aria-hidden={detailText ? undefined : true}
                          >
                            {detailText}
                          </div>
                        </div>
                      </div>

                      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
                        <StatusBadge status={status} />
                        <Button
                          type="button"
                          size="sm"
                          variant={status === "ready" ? "default" : "outline"}
                          className="h-7 px-2"
                          disabled={
                            !enabled ||
                            status === "checking" ||
                            installingEntryId !== null ||
                            statusLoading
                          }
                          onClick={() => handleEntryAction(entry)}
                        >
                          <ActionIcon status={status} result={result} />
                          {installing
                            ? t("ingress.action.installing")
                            : actionLabel(status, t, result)}
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      </div>

      {SHOW_RUNTIME_OVERVIEW && (
        <SectionCard title={t("ingress.runtimeOverview")}>
          <div className="grid gap-4 xl:grid-cols-[17rem_minmax(0,1.1fr)_minmax(0,1fr)]">
            <div className="space-y-4">
              <StatRows highlightFirst rows={overviewRows} />
              <div className="border-t pt-3">
                <StatRows rows={settingsRows} />
              </div>
            </div>

            <div className="min-w-0">
              <div className="mb-2 text-xs font-medium text-muted-foreground">
                {t("ingress.throughput")}
              </div>
              <LineChart labels={throughput.labels} values={throughput.values} />
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-1">
              <div className="min-w-0">
                <div className="mb-2 text-xs font-medium text-muted-foreground">
                  {t("ingress.contribution")}
                </div>
                {contribution.length > 0 ? (
                  <BarList items={contribution} />
                ) : (
                  <p className="text-xs text-muted-foreground">{t("common.empty")}</p>
                )}
              </div>
              <div className="min-w-0">
                <div className="mb-2 text-xs font-medium text-muted-foreground">
                  {t("ingress.events")}
                </div>
                {events.length > 0 ? (
                  <StatRows rows={events} />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {t("ingress.events.empty")}
                  </p>
                )}
              </div>
            </div>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
