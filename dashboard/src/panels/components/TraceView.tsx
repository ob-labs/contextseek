import { useMemo } from "react";

import { HelpHint } from "@/components/common/HelpHint";
import { Card, CardContent } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import type { RetrievalTrace } from "@/lib/types";

interface TraceNode {
  scope: string;
  label: string;
  score: number;
  order: number;
  items: number;
  children: TraceNode[];
}

/**
 * Visualize a hierarchical retrieval descent as a directory tree. Each visited
 * scope shows the order it was reached, its propagated score (as a bar), and how
 * many L2 items were collected there.
 */
export function TraceView({ trace }: { trace: RetrievalTrace }) {
  const { t } = useI18n();
  const { roots, converged } = useMemo(() => buildTree(trace), [trace]);
  if (roots.length === 0) return null;

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{t("trace.title")}</h3>
          <div className="flex items-center gap-2">
            <HelpHint content={t("trace.hint")} />
            {converged && (
              <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-600">
                {t("trace.converged")}
              </span>
            )}
          </div>
        </div>
        <div className="space-y-1">
          {roots.map((n) => (
            <TraceRow key={n.scope} node={n} depth={0} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function TraceRow({ node, depth }: { node: TraceNode; depth: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(node.score * 100)));
  const showScore = depth > 0;
  return (
    <>
      <div
        className="flex items-center gap-2 text-xs"
        style={{ paddingLeft: `${depth * 16}px` }}
      >
        <span className="w-5 shrink-0 text-right text-muted-foreground">
          {node.order > 0 ? node.order : ""}
        </span>
        <span className="font-mono">{node.label || "/"}</span>
        <div className="h-1.5 w-24 shrink-0 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
        </div>
        <span className="w-10 shrink-0 tabular-nums text-muted-foreground">
          {showScore ? `${pct}%` : "—"}
        </span>
        {node.items > 0 && (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">
            {node.items} item{node.items === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {node.children.map((c) => (
        <TraceRow key={c.scope} node={c} depth={depth + 1} />
      ))}
    </>
  );
}

function buildTree(trace: RetrievalTrace): { roots: TraceNode[]; converged: boolean } {
  const nodes = new Map<string, TraceNode>();
  let order = 0;
  let converged = false;

  const ensure = (scope: string): TraceNode => {
    let n = nodes.get(scope);
    if (!n) {
      const parts = scope.split("/");
      n = {
        scope,
        label: parts[parts.length - 1],
        score: 0,
        order: 0,
        items: 0,
        children: [],
      };
      nodes.set(scope, n);
    }
    return n;
  };

  for (const ev of trace.events) {
    if (ev.type === "descend" && ev.scope) {
      const n = ensure(ev.scope);
      n.score = ev.score;
      n.order = ++order;
    } else if (ev.type === "node_score" && ev.scope) {
      const n = ensure(ev.scope);
      if (n.order === 0) n.score = ev.score; // candidate not (yet) visited
    } else if (ev.type === "leaf_recall" && ev.scope) {
      const n = ensure(ev.scope);
      const got = Number(ev.data?.items ?? 0);
      if (got > 0) n.items += got;
    } else if (ev.type === "converged") {
      converged = true;
    }
  }

  // Link children to parents by path prefix; nodes without a known parent in the
  // set become roots.
  const roots: TraceNode[] = [];
  for (const n of nodes.values()) {
    const parentScope = n.scope.includes("/")
      ? n.scope.slice(0, n.scope.lastIndexOf("/"))
      : "";
    const parent = parentScope ? nodes.get(parentScope) : undefined;
    if (parent) parent.children.push(n);
    else roots.push(n);
  }
  const byOrder = (a: TraceNode, b: TraceNode) =>
    (a.order || 1e9) - (b.order || 1e9) || b.score - a.score;
  roots.sort(byOrder);
  for (const n of nodes.values()) n.children.sort(byOrder);
  return { roots, converged };
}
