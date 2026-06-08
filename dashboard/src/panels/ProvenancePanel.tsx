import { ArrowLeft, GitGraph } from "lucide-react";
import { useEffect, useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ctx } from "@/lib/ctxClient";
import { useScope } from "@/context/ScopeContext";
import { useNav } from "@/context/NavContext";
import { useAsyncFn } from "@/lib/utils";
import type { EvidenceChain, UpstreamResponse } from "@/lib/types";
import { EvidenceDetails } from "./components/EvidenceDetails";
import { EvidenceGraph } from "./components/EvidenceGraph";
import { ItemCard } from "./components/ItemCard";

export function ProvenancePanel({ initialItemId = "" }: { initialItemId?: string }) {
  const { scope } = useScope();
  const { back, canGoBack } = useNav();
  const [itemId, setItemId] = useState(initialItemId);
  const [maxDepth, setMaxDepth] = useState(10);

  const chain = useAsyncFn<EvidenceChain>(ctx.evidenceChain);
  const upstream = useAsyncFn<UpstreamResponse>(ctx.upstream);

  const inspect = () => {
    const id = itemId.trim();
    if (!id) return;
    chain.run({ scope, item_id: id, max_depth: maxDepth });
    upstream.run({ scope, item_id: id });
  };

  // Auto-run when navigated here with a pre-filled id.
  useEffect(() => {
    if (initialItemId) {
      setItemId(initialItemId);
      chain.run({ scope, item_id: initialItemId, max_depth: maxDepth });
      upstream.run({ scope, item_id: initialItemId });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialItemId]);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      {canGoBack && (
        <Button variant="ghost" size="sm" onClick={back} className="-ml-1">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
      )}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-6">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="pv-id">item_id</Label>
            <Input
              id="pv-id"
              value={itemId}
              onChange={(e) => setItemId(e.target.value)}
              placeholder="粘贴条目 id"
              className="font-mono"
              onKeyDown={(e) => {
                if (e.key === "Enter") inspect();
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pv-depth">max_depth</Label>
            <Input
              id="pv-depth"
              type="number"
              min={1}
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value) || 1)}
              className="w-24"
            />
          </div>
          <AsyncButton loading={chain.loading} onClick={inspect} disabled={!itemId.trim()}>
            <GitGraph className="h-4 w-4" /> 分析
          </AsyncButton>
        </CardContent>
      </Card>

      <EmptyState loading={chain.loading} error={chain.error} empty={!chain.data}>
        {chain.data && (
          <Tabs defaultValue="graph">
            <TabsList>
              <TabsTrigger value="graph">证据链图</TabsTrigger>
              <TabsTrigger value="details">详情</TabsTrigger>
              <TabsTrigger value="upstream">
                上游来源{upstream.data ? ` (${upstream.data.items.length})` : ""}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="graph">
              <EvidenceGraph chain={chain.data} />
            </TabsContent>
            <TabsContent value="details">
              <EvidenceDetails chain={chain.data} />
            </TabsContent>
            <TabsContent value="upstream">
              <EmptyState
                loading={upstream.loading}
                error={upstream.error}
                empty={Boolean(upstream.data && upstream.data.items.length === 0)}
                emptyText="无上游来源"
              >
                <div className="space-y-3">
                  {upstream.data?.items.map((item) => (
                    <ItemCard key={item.id} item={item} />
                  ))}
                </div>
              </EmptyState>
            </TabsContent>
          </Tabs>
        )}
      </EmptyState>
    </div>
  );
}
