import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { BarList } from "@/components/charts/BarList";
import { LineChart } from "@/components/charts/LineChart";
import { StatRows } from "@/components/charts/StatRows";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useScope } from "@/context/ScopeContext";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import type { Config, ContextItem } from "@/lib/types";

// source_type → gradient color
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

// Static DataPlug type catalog (SDK-shipped plug types)
const STATIC_PLUGS = [
  { name: "PowerMem", tagKey: "ingress.tag.session" },
  { name: "RAG", tagKey: "ingress.tag.retrieval" },
  { name: "Trace", tagKey: "ingress.tag.ide" },
  { name: "URL", tagKey: "ingress.tag.web" },
  { name: "MCP", tagKey: "ingress.tag.ide" },
  { nameKey: "ingress.source.note", tagKey: "ingress.tag.text" },
] as { name?: string; nameKey?: string; tagKey: string }[];

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

/** Build 7-day daily item count from items' created_at timestamps. */
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

/** Aggregate items by provenance.source_type, sorted descending by count. */
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

/** Format ISO 8601 datetime to HH:MM local time. */
function toHHMM(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Build event log rows from the most recently created items. */
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
    const preview = raw.length > 40 ? `${raw.slice(0, 40)}…` : raw;
    return { label: toHHMM(item.created_at), value: `[${st}] ${preview}` };
  });
}

export function IngressPanel() {
  const { t } = useI18n();
  const { scope } = useScope();

  const [config, setConfig] = useState<Config | null>(null);
  const [items, setItems] = useState<ContextItem[]>([]);

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

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  // Derived stats
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

  // Status overview rows — real data from /items + /config
  const overviewRows = [
    { label: t("ingress.status.totalItems"), value: String(items.length) },
    {
      label: t("ingress.status.watchPaths"),
      value: config ? String(config.watch_paths.length) : "—",
    },
    {
      label: t("ingress.status.latestWrite"),
      value: latestItem ? toHHMM(latestItem.created_at) : "—",
    },
    {
      label: t("ingress.status.autoSync"),
      value:
        config == null ? (
          "—"
        ) : (
          <Badge variant={config.auto_sync ? "secondary" : "outline"}>
            {config.auto_sync ? "on" : "off"}
          </Badge>
        ),
    },
  ];

  // Config section rows — real data from /config
  const settingsRows = [
    {
      label: t("ingress.config.defaultScope"),
      value: config?.default_scope ?? "—",
    },
    {
      label: t("ingress.config.lifecycle"),
      value:
        config?.lifecycle_interval_seconds != null
          ? `${config.lifecycle_interval_seconds}s`
          : "—",
    },
    {
      label: t("ingress.config.autoSync"),
      value:
        config == null ? (
          "—"
        ) : (
          <Badge variant={config.auto_sync ? "secondary" : "outline"}>
            {config.auto_sync ? "on" : "off"}
          </Badge>
        ),
    },
  ];

  // DataPlug catalog: static entries + watch_paths as File Watch entries
  const watchPlugs = (config?.watch_paths ?? []).map((wp) => ({
    name: wp.path.split("/").pop() || wp.path,
    tagKey: "ingress.tag.file",
  }));
  const catalogEntries: { name?: string; nameKey?: string; tagKey: string }[] = [
    ...STATIC_PLUGS,
    ...watchPlugs,
  ];

  // Watch paths as label/value rows
  const watchPathRows = (config?.watch_paths ?? []).map((wp) => ({
    label: wp.path,
    value: (
      <span className="font-mono text-xs text-muted-foreground">{wp.scope}</span>
    ),
  }));

  return (
    <div className="space-y-4 p-6">
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Column 1: plug catalog + status overview */}
        <div className="space-y-4">
          <SectionCard title={t("ingress.dataplug")}>
            <div className="grid grid-cols-2 gap-1.5">
              {catalogEntries.map((s, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-2 rounded-md border bg-muted/40 px-2 py-1.5 text-xs"
                >
                  <span>{s.nameKey ? t(s.nameKey) : s.name}</span>
                  <span className="text-muted-foreground">{t(s.tagKey)}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 grid gap-2 rounded-lg border border-dashed p-2 text-center text-xs">
              <div className="rounded-md border bg-muted/40 px-2 py-1.5">
                {t("ingress.flow.input")}
              </div>
              <div className="text-muted-foreground">{t("ingress.flow.normalize")}</div>
              <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1.5">
                {t("ingress.flow.item")}
              </div>
              <div className="text-muted-foreground">{t("ingress.flow.toPipeline")}</div>
              <div className="rounded-md border bg-muted/40 px-2 py-1.5">
                {t("ingress.flow.stages")}
              </div>
            </div>
          </SectionCard>

          <SectionCard title={t("ingress.statusOverview")}>
            <StatRows highlightFirst rows={overviewRows} />
          </SectionCard>
        </div>

        {/* Column 2: watch paths + throughput chart + contribution */}
        <div className="space-y-4">
          <SectionCard title={t("ingress.watchPaths")}>
            {watchPathRows.length > 0 ? (
              <StatRows rows={watchPathRows} />
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("ingress.watchPaths.empty")}
              </p>
            )}
          </SectionCard>

          <SectionCard title={t("ingress.throughput")}>
            <LineChart labels={throughput.labels} values={throughput.values} color="#54b6ff" />
          </SectionCard>

          <SectionCard title={t("ingress.contribution")}>
            {contribution.length > 0 ? (
              <BarList items={contribution} />
            ) : (
              <p className="text-xs text-muted-foreground">{t("common.empty")}</p>
            )}
          </SectionCard>
        </div>

        {/* Column 3: config settings + event log */}
        <div className="space-y-4">
          <SectionCard title={t("ingress.config")}>
            <StatRows rows={settingsRows} />
          </SectionCard>

          <SectionCard title={t("ingress.events")}>
            {events.length > 0 ? (
              <StatRows rows={events} />
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("ingress.events.empty")}
              </p>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
