import { useCallback, useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import {
  Bot,
  Braces,
  Database,
  Eye,
  EyeOff,
  Info,
  Plug,
  RefreshCw,
  SlidersHorizontal,
  X,
} from "lucide-react";

import { StatRows } from "@/components/charts/StatRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import type { Config, ConfigTestResponse, ConfigUpdateRequest, Health } from "@/lib/types";

const HEALTH_POLL_MS = 15_000;
type ProviderOption = {
  value: string;
  label: string;
};

const LLM_PROVIDER_OPTIONS: ProviderOption[] = [
  { value: "none", label: "none" },
  { value: "openai", label: "openai" },
  { value: "dashscope", label: "dashscope" },
  { value: "ollama", label: "ollama" },
];
const EMBEDDING_PROVIDER_OPTIONS = [
  { value: "none", label: "none" },
  { value: "openai", label: "openai" },
  { value: "dashscope", label: "dashscope" },
  { value: "ollama", label: "ollama" },
  { value: "huggingface", label: "huggingface" },
];

function toUiProvider(provider: string | undefined) {
  if (!provider || provider === "langchain") return "openai";
  return provider;
}

function isDesktopApp() {
  const maybeWindow = window as Window & {
    __TAURI__?: { core?: { invoke?: unknown } };
  };
  return Boolean(maybeWindow.__TAURI__?.core?.invoke);
}

function SettingsGroup({
  icon: Icon,
  title,
  desc,
  children,
}: {
  icon: LucideIcon;
  title: string;
  desc: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <p className="text-xs text-muted-foreground">{desc}</p>
      <Card>
        <CardContent className="p-4">{children}</CardContent>
      </Card>
    </section>
  );
}

/** Single editable row: shows Input in edit mode, plain value otherwise. */
function EditableRow({
  label,
  value,
  draftValue,
  isEditing,
  isPassword,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  draftValue: string | undefined;
  isEditing: boolean;
  isPassword?: boolean;
  placeholder?: string;
  onChange: (val: string) => void;
}) {
  const [showPlain, setShowPlain] = useState(false);

  if (!isEditing) {
    const display = isPassword && value ? "••••" + value.slice(-4) : value || "—";
    return (
      <div className="flex items-center justify-between py-1.5">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-xs font-medium">{display}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <div className="relative flex-1">
        <Input
          className="h-7 pr-8 text-xs"
          type={isPassword && !showPlain ? "password" : "text"}
          value={draftValue ?? value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        {isPassword && (
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            onClick={() => setShowPlain((p) => !p)}
          >
            {showPlain ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
    </div>
  );
}

function ProviderSelectRow({
  label,
  value,
  options,
  isEditing,
  onChange,
}: {
  label: string;
  value: string;
  options: ProviderOption[];
  isEditing: boolean;
  onChange: (value: string) => void;
}) {
  const display = value ? value : "none";
  const selectValue =
    options.some((option) => option.value === display) ? display : "openai";
  const displayLabel =
    options.find((option) => option.value === display)?.label ??
    (display === "langchain" ? "openai" : display);
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {isEditing ? (
        <Select value={selectValue} onValueChange={onChange}>
          <SelectTrigger className="h-7 w-44 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option.value} value={option.value} className="text-xs">
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <span className="text-xs font-medium">{displayLabel}</span>
      )}
    </div>
  );
}

function ConnectionTestRow({
  label,
  result,
  testing,
  onTest,
}: {
  label: string;
  result: ConfigTestResponse | null;
  testing: boolean;
  onTest: () => void;
}) {
  const stateLabel = testing ? "Testing" : result ? (result.ok ? "Connected" : "Failed") : "Not tested";
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
        <span
          className={`truncate text-right text-xs ${
            result
              ? result.ok
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-destructive"
              : "text-muted-foreground"
          }`}
          title={result?.message ?? stateLabel}
        >
          {result?.message ?? stateLabel}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          disabled={testing}
          onClick={onTest}
        >
          {testing && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
          Test
        </Button>
      </div>
    </div>
  );
}

export function SettingsPanel() {
  const { t } = useI18n();
  const [config, setConfig] = useState<Config | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const healthTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Edit mode state
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<ConfigUpdateRequest>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [showRestartDialog, setShowRestartDialog] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [testingTarget, setTestingTarget] = useState<"llm" | "embedding" | null>(null);
  const [llmTestResult, setLlmTestResult] = useState<ConfigTestResponse | null>(null);
  const [embeddingTestResult, setEmbeddingTestResult] = useState<ConfigTestResponse | null>(null);

  const fetchAll = useCallback(async () => {
    setError(false);
    const [cfgResult, healthResult] = await Promise.allSettled([
      ctx.config(),
      ctx.health(),
    ]);
    if (cfgResult.status === "fulfilled") setConfig(cfgResult.value);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    if (cfgResult.status === "rejected" && healthResult.status === "rejected") {
      setError(true);
    }
  }, []);

  const pollHealth = useCallback(async () => {
    try {
      const h = await ctx.health();
      setHealth(h);
      setError(false);
    } catch {
      // keep last known value on transient errors
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchAll().then(() => {
      if (cancelled) return;
      healthTimerRef.current = setInterval(() => {
        if (!cancelled) pollHealth();
      }, HEALTH_POLL_MS);
    });
    return () => {
      cancelled = true;
      if (healthTimerRef.current !== null) {
        clearInterval(healthTimerRef.current);
        healthTimerRef.current = null;
      }
    };
  }, [fetchAll, pollHealth]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchAll();
    setRefreshing(false);
  };

  const handleEdit = () => {
    setSaveError("");
    setDraft({});
    setIsEditing(true);
  };

  const handleCancel = () => {
    setSaveError("");
    setDraft({});
    setIsEditing(false);
  };

  const handleSave = async () => {
    const effectiveEmbeddingProvider =
      draft.embedding_provider ?? currentEmbeddingProvider;
    const effectiveEmbeddingModel = (
      draft.embedding_model ?? config?.embedding_model ?? ""
    ).trim();
    if (
      effectiveEmbeddingProvider !== "none" &&
      effectiveEmbeddingModel.toLowerCase() === "none"
    ) {
      setSaveError(t("settings.embedder.modelRequired"));
      return;
    }
    if (Object.keys(draft).length === 0) {
      setIsEditing(false);
      return;
    }
    setIsSaving(true);
    setSaveError("");
    try {
      const res = await ctx.updateConfig(draft);
      await fetchAll();
      setIsEditing(false);
      setDraft({});
      if (res.restart_required) setShowRestartDialog(true);
    } finally {
      setIsSaving(false);
    }
  };

  const handleRestartNow = async () => {
    setIsRestarting(true);
    try {
      await ctx.restart();
    } catch {
      // server will be down immediately after restart, ignore errors
    }
    // Poll health until server comes back
    const poll = async () => {
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const h = await ctx.health();
          if (h.status === "ok") {
            setHealth(h);
            await fetchAll();
            setIsRestarting(false);
            setShowRestartDialog(false);
            return;
          }
        } catch {
          // still down, keep polling
        }
      }
      // timed out, give up
      setIsRestarting(false);
      setShowRestartDialog(false);
    };
    poll();
  };

  const setField = <K extends keyof ConfigUpdateRequest>(key: K, val: string) => {
    setSaveError("");
    setDraft((prev) => {
      if (key === "embedding_provider") {
        if (val === "none") {
          return {
            ...prev,
            embedding_provider: "none",
            embedding_model: "none",
            embedding_dims: "0",
            embedding_base_url: "",
            embedding_api_key: "",
          };
        }
        return {
          ...prev,
          embedding_provider: val,
          embedding_model:
            (prev.embedding_model ?? config?.embedding_model) === "none"
              ? ""
              : prev.embedding_model,
        };
      }
      return { ...prev, [key]: val };
    });
    if (String(key).startsWith("llm_")) setLlmTestResult(null);
    if (String(key).startsWith("embedding_")) setEmbeddingTestResult(null);
  };

  // ── 包安装 ────────────────────────────────────────────────────────────────
  const BACKEND_PACKAGES: Record<string, string> = {
    seekdb: "pyseekdb",
    oceanbase: "contextseek[oceanbase]",
  };
  const [installState, setInstallState] = useState<"idle" | "installing" | "ok" | "error">("idle");
  const [installLog, setInstallLog] = useState("");

  const handleInstall = async (pkg: string) => {
    setInstallState("installing");
    setInstallLog("");
    try {
      const res = await ctx.installPackage(pkg);
      setInstallLog(res.stdout || res.stderr);
      setInstallState(res.status === "ok" ? "ok" : "error");
    } catch {
      setInstallState("error");
    }
  };

  // Reset install state when backend selection changes
  const effectiveBackend = (isEditing ? draft.storage_backend : undefined) ?? config?.storage_backend ?? "";
  useEffect(() => {
    setInstallState("idle");
    setInstallLog("");
  }, [effectiveBackend]);

  // ── 后端连接 ──────────────────────────────────────────────────────────────
  const addr =
    (import.meta.env.VITE_CTX_BASE as string | undefined) || "127.0.0.1:8000";

  const isOk = health?.status === "ok";
  const healthValue = (
    <span className="flex items-center gap-1.5 text-xs">
      <span
        className={`h-2 w-2 rounded-full ${
          health == null ? "bg-muted-foreground" : isOk ? "bg-emerald-500" : "bg-rose-500"
        }`}
      />
      {health == null ? "…" : isOk ? t("settings.sys.daemonValue") : health.status}
    </span>
  );

  const connection = [
    { label: t("settings.conn.addr"), value: addr },
    { label: t("settings.conn.health"), value: healthValue },
  ];

  // ── 系统控制 ──────────────────────────────────────────────────────────────
  const daemonStatus = health
    ? health.status === "ok"
      ? t("settings.sys.daemonValue")
      : health.status
    : "…";

  const autoSyncValue = config
    ? config.auto_sync
      ? t("settings.sys.autoSyncValue")
      : t("settings.sys.autoSyncOff")
    : "…";

  const system = [
    {
      label: t("settings.sys.daemon"),
      value: daemonStatus,
      variant: (health?.status === "ok" ? "default" : "destructive") as
        | "default"
        | "destructive"
        | "secondary",
    },
    {
      label: t("settings.sys.autoSync"),
      value: autoSyncValue,
      variant: (config === null
        ? "secondary"
        : config.auto_sync
          ? "default"
          : "secondary") as "default" | "destructive" | "secondary",
    },
  ];

  // ── 存储分组只读行 ─────────────────────────────────────────────────────────
  const val = (v: string | undefined) => (config ? v || "—" : "…");
  const dbBackend = effectiveBackend;
  const storageBackendOptions = isDesktopApp()
    ? ["memory", "file", "sqlite", "oceanbase"]
    : ["memory", "file", "sqlite", "seekdb", "oceanbase"];
  const seekdbMode = config?.seekdb_mode ?? "embedded";
  const currentLlmProvider = toUiProvider(
    config?.llm_provider ?? (config?.llm_model === "none" ? "none" : "openai"),
  );
  const currentEmbeddingProvider =
    toUiProvider(
      config?.embedding_provider ??
        (config?.embedding_model === "none" ? "none" : "openai"),
    );
  const llmProvider = (isEditing ? draft.llm_provider : undefined) ?? currentLlmProvider;
  const embeddingProvider =
    (isEditing ? draft.embedding_provider : undefined) ?? currentEmbeddingProvider;
  const llmEnabled = config ? llmProvider !== "none" : false;
  const embeddingEnabled = config ? embeddingProvider !== "none" : false;

  const handleTestLlm = async () => {
    setTestingTarget("llm");
    setLlmTestResult(null);
    try {
      const result = await ctx.testConfig({
        target: "llm",
        provider: llmProvider,
        model: draft.llm_model ?? config?.llm_model ?? "",
        base_url: draft.llm_base_url ?? config?.llm_base_url ?? "",
        api_key: draft.llm_api_key ?? config?.llm_api_key ?? "",
      });
      setLlmTestResult(result);
    } catch (exc) {
      setLlmTestResult({
        ok: false,
        message: exc instanceof Error ? exc.message : "Connection test failed.",
      });
    } finally {
      setTestingTarget(null);
    }
  };

  const handleTestEmbedding = async () => {
    setTestingTarget("embedding");
    setEmbeddingTestResult(null);
    try {
      const result = await ctx.testConfig({
        target: "embedding",
        provider: embeddingProvider,
        model: draft.embedding_model ?? config?.embedding_model ?? "",
        dims: draft.embedding_dims ?? config?.embedding_dims ?? "",
        base_url: draft.embedding_base_url ?? config?.embedding_base_url ?? "",
        api_key: draft.embedding_api_key ?? config?.embedding_api_key ?? "",
      });
      setEmbeddingTestResult(result);
    } catch (exc) {
      setEmbeddingTestResult({
        ok: false,
        message: exc instanceof Error ? exc.message : "Connection test failed.",
      });
    } finally {
      setTestingTarget(null);
    }
  };

  const aboutRows = [{ label: t("settings.about.version"), value: val(config?.version) }];

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      {/* Restart confirmation dialog */}
      <Dialog open={showRestartDialog} onOpenChange={(o) => { if (!isRestarting) setShowRestartDialog(o); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("settings.restart.title")}</DialogTitle>
            <DialogDescription>{t("settings.restart.desc")}</DialogDescription>
          </DialogHeader>
          {isRestarting && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <RefreshCw className="h-4 w-4 animate-spin" />
              {t("settings.restart.restarting")}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              disabled={isRestarting}
              onClick={() => setShowRestartDialog(false)}
            >
              {t("settings.restart.later")}
            </Button>
            <Button
              size="sm"
              disabled={isRestarting}
              onClick={handleRestartNow}
            >
              {t("settings.restart.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Top action bar */}
      <div className="flex items-center justify-end gap-2">
        {isEditing ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCancel}
              disabled={isSaving}
              className="gap-1.5 text-xs text-muted-foreground"
            >
              {t("settings.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={isSaving}
              className="gap-1.5 text-xs"
            >
              {isSaving && (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              )}
              {isSaving ? t("settings.saving") : t("settings.save")}
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshing}
              className="gap-1.5 text-xs text-muted-foreground"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
              {t("settings.refresh")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleEdit}
              className="gap-1.5 text-xs"
            >
              {t("settings.edit")}
            </Button>
          </>
        )}
      </div>

      {error && (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {t("settings.loadError")}
        </p>
      )}
      {saveError && (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {saveError}
        </p>
      )}

      {/* Backend connection (read-only) */}
      <SettingsGroup
        icon={Plug}
        title={t("settings.connection")}
        desc={t("settings.connection.desc")}
      >
        <StatRows highlightFirst rows={connection} />
      </SettingsGroup>

      {/* LLM group */}
      <SettingsGroup icon={Bot} title={t("settings.llm")} desc={t("settings.llm.desc")}>
        <div className="divide-y">
          <ProviderSelectRow
            label={t("settings.provider.label")}
            value={llmProvider}
            options={LLM_PROVIDER_OPTIONS}
            isEditing={isEditing}
            onChange={(value) => setField("llm_provider", value)}
          />
          {llmEnabled && (
            <>
              <EditableRow
                label={t("settings.llm.model")}
                value={config?.llm_model ?? ""}
                draftValue={draft.llm_model}
                isEditing={isEditing}
                placeholder="provider/model"
                onChange={(v) => setField("llm_model", v)}
              />
              <EditableRow
                label={t("settings.llm.baseUrl")}
                value={config?.llm_base_url ?? ""}
                draftValue={draft.llm_base_url}
                isEditing={isEditing}
                placeholder="https://api.openai.com/v1"
                onChange={(v) => setField("llm_base_url", v)}
              />
              <EditableRow
                label={t("settings.llm.apiKey")}
                value={config?.llm_api_key ?? ""}
                draftValue={draft.llm_api_key}
                isEditing={isEditing}
                isPassword
                placeholder="sk-..."
                onChange={(v) => setField("llm_api_key", v)}
              />
              <ConnectionTestRow
                label={t("settings.connectionTest")}
                result={llmTestResult}
                testing={testingTarget === "llm"}
                onTest={handleTestLlm}
              />
            </>
          )}
        </div>
      </SettingsGroup>

      {/* Embedder group */}
      <SettingsGroup icon={Braces} title={t("settings.embedder")} desc={t("settings.embedder.desc")}>
        <div className="divide-y">
          <ProviderSelectRow
            label={t("settings.provider.label")}
            value={embeddingProvider}
            options={EMBEDDING_PROVIDER_OPTIONS}
            isEditing={isEditing}
            onChange={(value) => setField("embedding_provider", value)}
          />
          {embeddingEnabled && (
            <>
              <EditableRow
                label={t("settings.embedder.model")}
                value={config?.embedding_model ?? ""}
                draftValue={draft.embedding_model}
                isEditing={isEditing}
                placeholder="provider/model"
                onChange={(v) => setField("embedding_model", v)}
              />
              <EditableRow
                label={t("settings.embedder.dims")}
                value={config?.embedding_dims ?? ""}
                draftValue={draft.embedding_dims}
                isEditing={isEditing}
                placeholder="auto"
                onChange={(v) => setField("embedding_dims", v)}
              />
              <EditableRow
                label={t("settings.embedder.baseUrl")}
                value={config?.embedding_base_url ?? ""}
                draftValue={draft.embedding_base_url}
                isEditing={isEditing}
                placeholder="https://api.openai.com/v1"
                onChange={(v) => setField("embedding_base_url", v)}
              />
              <EditableRow
                label={t("settings.embedder.apiKey")}
                value={config?.embedding_api_key ?? ""}
                draftValue={draft.embedding_api_key}
                isEditing={isEditing}
                isPassword
                placeholder="sk-..."
                onChange={(v) => setField("embedding_api_key", v)}
              />
              <ConnectionTestRow
                label={t("settings.connectionTest")}
                result={embeddingTestResult}
                testing={testingTarget === "embedding"}
                onTest={handleTestEmbedding}
              />
            </>
          )}
        </div>
      </SettingsGroup>

      {/* Storage group */}
      <SettingsGroup icon={Database} title={t("settings.db")} desc={t("settings.db.desc")}>
        <div className="divide-y">
          {/* backend type: read-only display; editable dropdown in edit mode */}
          {!isEditing ? (
            <div className="flex items-center justify-between py-1.5">
              <span className="text-xs text-muted-foreground">{t("settings.db.backend")}</span>
              <span className="text-xs font-medium">{val(config?.storage_backend)}</span>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 py-1">
              <span className="shrink-0 text-xs text-muted-foreground">{t("settings.db.backend")}</span>
              <select
                className="h-7 flex-1 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                value={draft.storage_backend ?? config?.storage_backend ?? ""}
                onChange={(e) => {
                  const next = e.target.value;
                  setDraft((prev) => {
                    const updated: Partial<ConfigUpdateRequest> = { ...prev, storage_backend: next };
                    if (next === "sqlite" && !prev.sqlite_path && !config?.sqlite_path) {
                      updated.sqlite_path = "~/.contextseek/contextseek.sqlite3";
                    }
                    if (next === "seekdb" && !prev.seekdb_path && !config?.seekdb_path) {
                      updated.seekdb_path = "~/.contextseek/seekdb.db";
                    }
                    return updated;
                  });
                }}
              >
                {storageBackendOptions.map((backend) => (
                  <option key={backend} value={backend}>
                    {backend}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Package install hint */}
          {isEditing && BACKEND_PACKAGES[dbBackend] && (
            <div className="flex items-center gap-2 py-2 text-xs">
              <span className="text-muted-foreground">
                {t("settings.db.requires")}:{" "}
                <code className="rounded bg-muted px-1">{BACKEND_PACKAGES[dbBackend]}</code>
              </span>
              {installState === "idle" && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 text-xs"
                  onClick={() => handleInstall(BACKEND_PACKAGES[dbBackend])}
                >
                  {t("settings.db.install")}
                </Button>
              )}
              {installState === "installing" && (
                <span className="flex items-center gap-1 text-muted-foreground">
                  <RefreshCw className="h-3 w-3 animate-spin" />
                  {t("settings.db.installing")}
                </span>
              )}
              {installState === "ok" && (
                <span className="text-emerald-600">{t("settings.db.installOk")}</span>
              )}
              {installState === "error" && (
                <span className="text-rose-600">{t("settings.db.installErr")}</span>
              )}
            </div>
          )}
          {installLog && (
            <pre className="max-h-32 overflow-auto rounded bg-muted p-2 text-xs text-muted-foreground">
              {installLog}
            </pre>
          )}

          {/* OceanBase */}
          {dbBackend === "oceanbase" && (
            <>
              <EditableRow
                label={t("settings.db.host")}
                value={config?.ob_host ?? ""}
                draftValue={draft.ob_host}
                isEditing={isEditing}
                onChange={(v) => setField("ob_host", v)}
              />
              <EditableRow
                label={t("settings.db.port")}
                value={config?.ob_port ?? ""}
                draftValue={draft.ob_port}
                isEditing={isEditing}
                onChange={(v) => setField("ob_port", v)}
              />
              <EditableRow
                label={t("settings.db.dbName")}
                value={config?.ob_db_name ?? ""}
                draftValue={draft.ob_db_name}
                isEditing={isEditing}
                onChange={(v) => setField("ob_db_name", v)}
              />
              <EditableRow
                label={t("settings.db.tableName")}
                value={config?.ob_table_name ?? ""}
                draftValue={draft.ob_table_name}
                isEditing={isEditing}
                onChange={(v) => setField("ob_table_name", v)}
              />
            </>
          )}

          {/* SeekDB server mode */}
          {dbBackend === "seekdb" && seekdbMode === "server" && (
            <>
              <EditableRow
                label={t("settings.db.host")}
                value={config?.seekdb_host ?? ""}
                draftValue={draft.seekdb_host}
                isEditing={isEditing}
                onChange={(v) => setField("seekdb_host", v)}
              />
              <EditableRow
                label={t("settings.db.port")}
                value={config?.seekdb_port ?? ""}
                draftValue={draft.seekdb_port}
                isEditing={isEditing}
                onChange={(v) => setField("seekdb_port", v)}
              />
              <EditableRow
                label={t("settings.db.dbName")}
                value={config?.seekdb_database ?? ""}
                draftValue={draft.seekdb_database}
                isEditing={isEditing}
                onChange={(v) => setField("seekdb_database", v)}
              />
            </>
          )}

          {/* SeekDB embedded mode */}
          {dbBackend === "seekdb" && seekdbMode === "embedded" && (
            <EditableRow
              label={t("settings.db.path")}
              value={config?.seekdb_path ?? ""}
              draftValue={draft.seekdb_path}
              isEditing={isEditing}
              onChange={(v) => setField("seekdb_path", v)}
            />
          )}

          {/* SQLite */}
          {dbBackend === "sqlite" && (
            <EditableRow
              label={t("settings.db.path")}
              value={config?.sqlite_path ?? ""}
              draftValue={draft.sqlite_path}
              isEditing={isEditing}
              onChange={(v) => setField("sqlite_path", v)}
            />
          )}

          {/* File */}
          {dbBackend === "file" && (
            <EditableRow
              label={t("settings.db.path")}
              value={config?.storage_path ?? ""}
              draftValue={draft.storage_path}
              isEditing={isEditing}
              onChange={(v) => setField("storage_path", v)}
            />
          )}
        </div>
      </SettingsGroup>

      {/* System (read-only) */}
      <SettingsGroup
        icon={SlidersHorizontal}
        title={t("settings.system")}
        desc={t("settings.system.desc")}
      >
        <StatRows
          rows={system.map((s) => ({
            label: s.label,
            value: <Badge variant={s.variant}>{s.value}</Badge>,
          }))}
        />
      </SettingsGroup>

      {/* About (read-only) */}
      <SettingsGroup icon={Info} title={t("settings.about")} desc={t("settings.about.desc")}>
        <StatRows rows={aboutRows} />
      </SettingsGroup>
    </div>
  );
}
