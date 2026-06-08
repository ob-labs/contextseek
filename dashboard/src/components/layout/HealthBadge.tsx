import { useCallback, useEffect, useState } from "react";

import { ctx } from "@/lib/ctxClient";
import { cn } from "@/lib/utils";
import { useScope } from "@/context/ScopeContext";

/** Polls /health and /overview to show a small live indicator in the topbar. */
export function HealthBadge() {
  const { scope } = useScope();
  const [ok, setOk] = useState<boolean | null>(null);
  const [version, setVersion] = useState<string>("");
  const [total, setTotal] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const health = await ctx.health();
      setOk(health.status === "ok");
      setVersion(health.version);
    } catch {
      setOk(false);
    }
    try {
      const overview = await ctx.overview(scope);
      setTotal(overview.total_items);
    } catch {
      setTotal(null);
    }
  }, [scope]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const color = ok == null ? "bg-muted-foreground" : ok ? "bg-emerald-500" : "bg-rose-500";

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground" title={`ctx ${version}`}>
      <span className={cn("h-2 w-2 rounded-full", color)} />
      <span>{ok == null ? "检查中" : ok ? "在线" : "离线"}</span>
      {total != null && (
        <span className="font-mono tabular-nums">· {total} 条</span>
      )}
    </div>
  );
}
