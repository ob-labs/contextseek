import { Search } from "lucide-react";
import { useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { EmptyState } from "@/components/common/EmptyState";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ctx } from "@/lib/ctxClient";
import { useScope } from "@/context/ScopeContext";
import { useAsyncFn } from "@/lib/utils";
import type { RetrieveResponse } from "@/lib/types";
import { HitCard } from "./components/HitCard";

export function RetrievePanel() {
  const { scope } = useScope();
  const [query, setQuery] = useState("");
  const [k, setK] = useState(10);
  const [full, setFull] = useState(false);
  const [includeDeleted, setIncludeDeleted] = useState(false);
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
        setFilterError("filters 不是合法 JSON");
        return;
      }
    }
    run({ scope, query, k, full, include_deleted: includeDeleted, filters });
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1.5">
            <Label htmlFor="q">查询</Label>
            <Textarea
              id="q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入自然语言查询…"
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
              直接取全文 (full)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeDeleted}
                onChange={(e) => setIncludeDeleted(e.target.checked)}
              />
              含已删除
            </label>
            <AsyncButton loading={loading} onClick={submit} disabled={!query.trim()}>
              <Search className="h-4 w-4" /> 检索
            </AsyncButton>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">高级：filters (JSON)</summary>
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
          layer={data._meta.layer} · full_via={data._meta.full_via}
          {data._meta.hint && <> · {data._meta.hint}</>}
        </div>
      )}

      <EmptyState
        loading={loading}
        error={error}
        empty={Boolean(data && data.items.length === 0)}
        emptyText="没有命中结果"
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
