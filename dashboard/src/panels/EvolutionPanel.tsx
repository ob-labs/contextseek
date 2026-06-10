import { Moon, Sparkles } from "lucide-react";
import { useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";
import type { CompactResponse, DreamResponse } from "@/lib/types";

export function EvolutionPanel() {
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <EvolutionCard />
    </div>
  );
}

function EvolutionCard() {
  const { t } = useI18n();
  const { scope } = useScope();
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState<unknown>(null);
  const [compact, setCompact] = useState<CompactResponse | null>(null);
  const [dream, setDream] = useState<DreamResponse | null>(null);

  const runCompact = async () => {
    setBusy("compact");
    setError(null);
    try {
      setCompact(await ctx.compact({ scope, dry_run: dryRun }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy("");
    }
  };

  const runDream = async () => {
    setBusy("dream");
    setError(null);
    try {
      setDream(await ctx.dream({ scope, dry_run: dryRun }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy("");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("evolution.title")}</CardTitle>
        <CardDescription>{t("evolution.desc", { scope })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          {t("evolution.dryRun")}
        </label>
        <div className="flex flex-wrap gap-3">
          <AsyncButton variant="outline" loading={busy === "compact"} onClick={runCompact}>
            <Sparkles className="h-4 w-4" /> Compact
          </AsyncButton>
          <AsyncButton variant="outline" loading={busy === "dream"} onClick={runDream}>
            <Moon className="h-4 w-4" /> Dream
          </AsyncButton>
        </div>
        {error ? <p className="text-sm text-destructive">{errorMessage(error)}</p> : null}
        {compact && (
          <Stats
            title={t("evolution.compactResult")}
            entries={[
              ["merged", compact.merged],
              ["archived", compact.archived],
              ["evolved", compact.evolved],
            ]}
          />
        )}
        {dream && (
          <Stats
            title={t("evolution.dreamResult")}
            entries={[
              ["total", dream.total_dream_items],
              ["consol. patterns", dream.consolidation_patterns],
              ["consol. items", dream.consolidation_items],
              ["divergence", dream.divergence_items],
            ]}
          />
        )}
      </CardContent>
    </Card>
  );
}

function Stats({ title, entries }: { title: string; entries: [string, number][] }) {
  return (
    <div className="rounded-md border bg-muted/40 p-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div className="flex flex-wrap gap-4">
        {entries.map(([label, value]) => (
          <div key={label} className="flex flex-col">
            <span className="font-mono text-lg tabular-nums">{value}</span>
            <span className="text-xs text-muted-foreground">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
