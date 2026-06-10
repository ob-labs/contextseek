import { useCallback, useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Cpu, Plug, RefreshCw, SlidersHorizontal } from "lucide-react";

import { StatRows } from "@/components/charts/StatRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import type { Config, Health } from "@/lib/types";

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

export function SettingsPanel() {
  const { t } = useI18n();
  const [config, setConfig] = useState<Config | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const healthTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    setError(false);
    const [cfgResult, healthResult] = await Promise.allSettled([
      ctx.config(),
      ctx.health(),
    ]);
    if (cfgResult.status === "fulfilled") {
      setConfig(cfgResult.value);
    }
    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
    }
    if (
      cfgResult.status === "rejected" &&
      healthResult.status === "rejected"
    ) {
      setError(true);
    }
  }, []);

  const pollHealth = useCallback(async () => {
    try {
      const h = await ctx.health();
      setHealth(h);
      setError(false);
    } catch {
      // keep last known health value; don't blank it on transient errors
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

  const addr =
    (import.meta.env.VITE_CTX_BASE as string | undefined) ||
    (typeof window !== "undefined" ? window.location.host : "127.0.0.1:39082");

  const connection = [{ label: t("settings.conn.addr"), value: addr }];

  const watchPathsValue = (() => {
    if (!config) return "…";
    if (!config.watch_paths || config.watch_paths.length === 0) return "—";
    return (
      <ul className="space-y-0.5">
        {config.watch_paths.map((wp) => (
          <li key={wp.path} className="font-mono text-xs">
            {wp.path}
            <span className="mx-1 text-muted-foreground">→</span>
            <span className="text-muted-foreground">{wp.scope}</span>
          </li>
        ))}
      </ul>
    );
  })();

  const modelRows = config
    ? [
        { label: "LLM", value: config.llm_model || "—" },
        { label: "embedding", value: config.embedding_model || "—" },
        { label: "storage", value: config.storage_backend || "—" },
        { label: "version", value: config.version || "—" },
        { label: t("settings.model.watchPaths"), value: watchPathsValue },
      ]
    : [
        { label: "LLM", value: "…" },
        { label: "embedding", value: "…" },
        { label: "storage", value: "…" },
        { label: "version", value: "…" },
        { label: t("settings.model.watchPaths"), value: "…" },
      ];

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

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div />
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
      </div>

      {error && (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {t("settings.loadError")}
        </p>
      )}

      <SettingsGroup
        icon={Plug}
        title={t("settings.connection")}
        desc={t("settings.connection.desc")}
      >
        <StatRows highlightFirst rows={connection} />
      </SettingsGroup>

      <SettingsGroup icon={Cpu} title={t("settings.model")} desc={t("settings.model.desc")}>
        <StatRows highlightFirst rows={modelRows} />
      </SettingsGroup>

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
    </div>
  );
}
