import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { BarList } from "@/components/charts/BarList";
import { DonutChart } from "@/components/charts/DonutChart";
import { LineChart } from "@/components/charts/LineChart";
import { StatCards } from "@/components/charts/StatCards";
import { StatRows } from "@/components/charts/StatRows";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { ctx } from "@/lib/ctxClient";
import type { GlobalOverview } from "@/lib/types";

const STAGE_COLORS: Record<string, string> = {
  raw: "#7e879f",
  extracted: "#54b6ff",
  knowledge: "#6ed18f",
  skill: "#b694ff",
};

const HEATMAP_BASE = "#54b6ff"; // blue tint for non-zero cells

function RelationHeatmap({
  stages,
  matrix,
}: {
  stages: string[];
  matrix: number[][];
}) {
  const { t } = useI18n();
  const maxVal = Math.max(1, ...matrix.flatMap((row) => row));
  const n = stages.length;

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[10px]">
        <thead>
          <tr>
            <th className="w-16 p-0" />
            {stages.map((s) => (
              <th key={s} className="p-1 text-center font-normal text-muted-foreground">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {stages.map((rowStage, r) => (
            <tr key={rowStage}>
              <td className="pr-1 text-right font-normal text-muted-foreground">{rowStage}</td>
              {Array.from({ length: n }, (_, c) => {
                const val = matrix[r]?.[c] ?? 0;
                const intensity = val / maxVal;
                return (
                  <td key={c} className="p-0.5">
                    <div
                      title={`${rowStage} → ${stages[c]}: ${val}`}
                      className="flex h-7 w-full items-center justify-center rounded text-[9px] font-medium transition-colors"
                      style={{
                        backgroundColor:
                          val > 0
                            ? `color-mix(in srgb, ${HEATMAP_BASE} ${Math.round(intensity * 80 + 10)}%, transparent)`
                            : "transparent",
                        color: intensity > 0.5 ? "#fff" : "var(--muted-foreground)",
                        border: "1px solid color-mix(in srgb, var(--border) 60%, transparent)",
                      }}
                    >
                      {val > 0 ? val : ""}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-right text-[9px] text-muted-foreground">
        {t("overview.heatmap.axis")}
      </p>
    </div>
  );
}

const SCOPE_GRADIENTS = [
  "linear-gradient(90deg,#4b8dff,#54b6ff)",
  "linear-gradient(90deg,#57a4ff,#7ac4ff)",
  "linear-gradient(90deg,#5bb58d,#6ed18f)",
  "linear-gradient(90deg,#9470ff,#b694ff)",
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

function SkeletonBlock({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-muted ${className ?? "h-32"}`} />;
}

export function OverviewPanel() {
  const { t } = useI18n();
  const [data, setData] = useState<GlobalOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    ctx
      .globalOverview()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="space-y-4 p-6">
        <SkeletonBlock className="h-20" />
        <div className="grid gap-4 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-40" />
          ))}
        </div>
      </div>
    );
  }

  // Derive display data from API response (or fall back to empty)
  const stageDist = data?.stage_distribution ?? {};
  const stages = ["raw", "extracted", "knowledge", "skill"];

  const kpis = [
    { label: t("overview.kpi.total"), value: data?.total_items ?? 0 },
    { label: t("overview.kpi.health"), value: data?.health_score ?? 0 },
    { label: t("overview.kpi.activeScopes"), value: data?.active_scopes ?? 0 },
  ];

  const funnel = stages.map((s) => ({ label: s, value: stageDist[s] ?? 0 }));

  const stageDonut = stages
    .filter((s) => (stageDist[s] ?? 0) > 0)
    .map((s) => ({ label: s, value: stageDist[s]!, color: STAGE_COLORS[s] ?? "#8888aa" }));

  const scopeTop = (data?.scope_top ?? []).slice(0, 5).map((s) => ({
    label: s.label,
    value: s.value,
  }));

  const scopeGrowth = (data?.scope_top ?? []).slice(0, 4).map((s, i) => ({
    label: s.label.split("/").pop() ?? s.label,
    value: s.value,
    color: SCOPE_GRADIENTS[i % SCOPE_GRADIENTS.length],
  }));

  const trend = data?.trend ?? { labels: [], values: [] };

  const conflictSubject = data?.risk_conflict_subject ?? null;
  const orphanRatio = data?.risk_orphan_ratio ?? null;
  const suggestCompact = data?.risk_suggest_compact ?? null;

  const risks = [
    {
      label: t("overview.risk.conflictSubject"),
      value: conflictSubject ?? "—",
    },
    {
      label: t("overview.risk.orphanRatio"),
      value: orphanRatio !== null ? `${(orphanRatio * 100).toFixed(1)}%` : "—",
    },
    {
      label: t("overview.risk.suggestCompact"),
      value:
        suggestCompact === null
          ? "—"
          : suggestCompact
            ? t("overview.risk.yes")
            : t("overview.risk.no"),
    },
  ];

  return (
    <div className="space-y-4 p-6">
      <StatCards cards={kpis} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4">
          <SectionCard title={t("overview.funnel")}>
            <StatRows highlightFirst rows={funnel} />
          </SectionCard>
          <SectionCard title={t("overview.stageShare")}>
            {stageDonut.length > 0 ? (
              <DonutChart segments={stageDonut} />
            ) : (
              <p className="text-xs text-muted-foreground">{t("overview.noData") || "No data"}</p>
            )}
          </SectionCard>
          <SectionCard title={t("overview.scopeTop")}>
            <StatRows rows={scopeTop} />
          </SectionCard>
        </div>

        <div className="space-y-4">
          <SectionCard title={t("overview.trend")}>
            <LineChart labels={trend.labels} values={trend.values} color="#54b6ff" />
          </SectionCard>
          <SectionCard title={t("overview.scopeGrowth")}>
            <BarList items={scopeGrowth} />
          </SectionCard>
        </div>

        <div className="space-y-4">
          <SectionCard title={t("overview.heatmap")}>
            {data?.heatmap ? (
              <RelationHeatmap
                stages={data.heatmap.stages}
                matrix={data.heatmap.matrix}
              />
            ) : (
              <div className="grid h-36 place-items-center rounded-lg border border-dashed text-xs text-muted-foreground">
                {t("overview.noData") || "No link data"}
              </div>
            )}
          </SectionCard>
          <SectionCard title={t("overview.risks")}>
            <StatRows rows={risks} />
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
