import { Search } from "lucide-react";
import { useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { EmptyState } from "@/components/common/EmptyState";
import { HelpHint } from "@/components/common/HelpHint";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { useScope } from "@/context/ScopeContext";
import { useAsyncFn } from "@/lib/utils";
import type { RetrieveResponse } from "@/lib/types";
import { HitCard } from "./components/HitCard";
import { TraceView } from "./components/TraceView";

export function RetrievePanel() {
  const { t } = useI18n();
  const { scope } = useScope();
  const [query, setQuery] = useState("");
  const [k, setK] = useState(10);
  const [full, setFull] = useState(false);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [includeTrace, setIncludeTrace] = useState(false);
  const [filtersText, setFiltersText] = useState("");
  const [filterError, setFilterError] = useState<string>("");

  const { data, loading, error, run } = useAsyncFn<RetrieveResponse>(ctx.retrieve);

  const submit = () => {
    setFilterError("");
    let filters: Record<string, unknown> | undefined;
    if (filtersText.trim()) {
      try {
        filters = JSON.parse(filtersText);
      } catch {
        setFilterError(t("retrieve.filterInvalid"));
        return;
      }
    }
    run({
      scope,
      query,
      k,
      full,
      include_deleted: includeDeleted,
      include_trace: includeTrace,
      filters,
    });
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1.5">
            <Label htmlFor="q">{t("retrieve.query")}</Label>
            <Textarea
              id="q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("retrieve.queryPlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
              }}
            />
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="k">k</Label>
              <Input
                id="k"
                type="number"
                min={1}
                value={k}
                onChange={(e) => setK(Number(e.target.value) || 1)}
                className="w-20"
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
              {t("retrieve.full")}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeDeleted}
                onChange={(e) => setIncludeDeleted(e.target.checked)}
              />
              {t("retrieve.includeDeleted")}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeTrace}
                onChange={(e) => setIncludeTrace(e.target.checked)}
              />
              {t("retrieve.trace")}
            </label>
            <AsyncButton loading={loading} onClick={submit} disabled={!query.trim()}>
              <Search className="h-4 w-4" /> {t("retrieve.action")}
            </AsyncButton>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">{t("retrieve.advanced")}</summary>
            <Textarea
              value={filtersText}
              onChange={(e) => setFiltersText(e.target.value)}
              placeholder='{"key": "value"}'
              className="mt-2 font-mono text-xs"
            />
            {filterError && <p className="mt-1 text-xs text-destructive">{filterError}</p>}
          </details>
        </CardContent>
      </Card>

      {data && (
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <div className="flex items-start justify-between gap-2">
            <span>
              layer={data._meta.layer} · full_via={data._meta.full_via}
              {data._meta.hint && <> · {data._meta.hint}</>}
            </span>
            <HelpHint content={t("retrieve.metaHint")} />
          </div>
        </div>
      )}

      {data?._trace && data._trace.events.length > 0 && (
        <TraceView trace={data._trace} />
      )}

      <EmptyState
        loading={loading}
        error={error}
        empty={Boolean(data && data.items.length === 0)}
        emptyText={t("retrieve.empty")}
      >
        <div className="space-y-3">
          {data?.items.map((hit) => (
            <HitCard key={hit.id} hit={hit} />
          ))}
        </div>
      </EmptyState>
    </div>
  );
}
