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
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import type { Config, ConfigUpdateRequest, Health } from "@/lib/types";

const HEALTH_POLL_MS = 15_000;

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

function ToggleRow({
  label,
  checked,
  isEditing,
  onLabel,
  offLabel,
  onChange,
}: {
  label: string;
  checked: boolean;
  isEditing: boolean;
  onLabel: string;
  offLabel: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {isEditing ? (
        <label className="flex items-center gap-2 text-xs font-medium">
          <input
            type="checkbox"
            className="h-4 w-4 accent-primary"
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
          />
          {checked ? onLabel : offLabel}
        </label>
      ) : (
        <span className="text-xs font-medium">{checked ? onLabel : offLabel}</span>
      )}
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
  const [showRestartDialog, setShowRestartDialog] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);

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
    setDraft({});
    setIsEditing(true);
  };

  const handleCancel = () => {
    setDraft({});
    setIsEditing(false);
  };

  const handleSave = async () => {
    if (Object.keys(draft).length === 0) {
      setIsEditing(false);
      return;
    }
    setIsSaving(true);
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
    setDraft((prev) => ({ ...prev, [key]: val }));
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
  const seekdbMode = config?.seekdb_mode ?? "embedded";
  const currentLlmProvider =
    config?.llm_provider ?? (config?.llm_model === "none" ? "none" : "langchain");
  const currentEmbeddingProvider =
    config?.embedding_provider ??
    (config?.embedding_model === "none" ? "none" : "langchain");
  const llmProvider = (isEditing ? draft.llm_provider : undefined) ?? currentLlmProvider;
  const embeddingProvider =
    (isEditing ? draft.embedding_provider : undefined) ?? currentEmbeddingProvider;
  const llmEnabled = config ? llmProvider !== "none" : false;
  const embeddingEnabled = config ? embeddingProvider !== "none" : false;

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
          <ToggleRow
            label={t("settings.provider.enabled")}
            checked={llmEnabled}
            isEditing={isEditing}
            onLabel={t("settings.provider.on")}
            offLabel={t("settings.provider.off")}
            onChange={(checked) => setField("llm_provider", checked ? "langchain" : "none")}
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
            </>
          )}
        </div>
      </SettingsGroup>

      {/* Embedder group */}
      <SettingsGroup icon={Braces} title={t("settings.embedder")} desc={t("settings.embedder.desc")}>
        <div className="divide-y">
          <ToggleRow
            label={t("settings.provider.enabled")}
            checked={embeddingEnabled}
            isEditing={isEditing}
            onLabel={t("settings.provider.on")}
            offLabel={t("settings.provider.off")}
            onChange={(checked) =>
              setField("embedding_provider", checked ? "langchain" : "none")
            }
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
                <option value="memory">memory</option>
                <option value="file">file</option>
                <option value="sqlite">sqlite</option>
                <option value="seekdb">seekdb</option>
                <option value="oceanbase">oceanbase</option>
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
