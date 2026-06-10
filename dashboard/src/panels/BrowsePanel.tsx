import { Database, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { EmptyState } from "@/components/common/EmptyState";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { STAGES, type ItemsResponse, type Stage } from "@/lib/types";
import { useScope } from "@/context/ScopeContext";
import { useAsyncFn } from "@/lib/utils";
import { ItemActions } from "./components/ItemActions";
import { ItemCard } from "./components/ItemCard";

const ALL = "__all__";

export function BrowsePanel() {
  const { t } = useI18n();
  const { scope } = useScope();
  const [stage, setStage] = useState<string>(ALL);
  const { data, loading, error, run } = useAsyncFn<ItemsResponse>(ctx.items);
  const [seeding, setSeeding] = useState(false);

  const load = useCallback(() => {
    run({ scope, stage: stage === ALL ? undefined : (stage as Stage) });
  }, [run, scope, stage]);

  useEffect(() => {
    load();
  }, [load]);

  const seed = useCallback(async () => {
    setSeeding(true);
    try {
      await ctx.seed();
      load();
    } finally {
      setSeeding(false);
    }
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <div className="flex items-center gap-3">
        <Select value={stage} onValueChange={setStage}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("browse.allStages")}</SelectItem>
            {STAGES.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <AsyncButton variant="outline" loading={loading} onClick={load}>
          <RefreshCw className="h-4 w-4" /> {t("browse.refresh")}
        </AsyncButton>
        <AsyncButton variant="outline" loading={seeding} onClick={seed}>
          <Database className="h-4 w-4" /> {t("browse.seed")}
        </AsyncButton>
        {data && (
          <span className="text-sm text-muted-foreground">
            {t("browse.count", { n: data.items.length })}
          </span>
        )}
      </div>

      <EmptyState
        loading={loading}
        error={error}
        empty={Boolean(data && data.items.length === 0)}
        emptyText={t("browse.empty")}
      >
        <div className="space-y-3">
          {data?.items.map((item) => (
            <ItemCard
              key={item.id}
              item={item}
              actions={<ItemActions itemId={item.id} onChanged={load} />}
            />
          ))}
        </div>
      </EmptyState>
    </div>
  );
}
