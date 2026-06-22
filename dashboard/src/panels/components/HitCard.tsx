import { GitGraph } from "lucide-react";
import { useState } from "react";

import { ConfidenceBar } from "@/components/common/ConfidenceBar";
import { HelpHint } from "@/components/common/HelpHint";
import { JsonView } from "@/components/common/JsonView";
import { AsyncButton } from "@/components/common/AsyncButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useNav } from "@/context/NavContext";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";
import type { SearchHit } from "@/lib/types";

export function HitCard({ hit }: { hit: SearchHit }) {
  const { t } = useI18n();
  const { scope } = useScope();
  const { navigate } = useNav();
  const [full, setFull] = useState<unknown>(hit.layer === "full" ? hit.content : null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ref = `contextseek://${hit.scope}/${hit.id}`;

  const expand = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await ctx.expand({ scope, ids: [hit.id] });
      setFull(res.items[0]?.content ?? t("hit.empty"));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="space-y-2 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <ConfidenceBar value={hit.score} label={t("hit.finalScore")} />
            <HelpHint content={t("hit.finalScoreHint")} />
            <Badge variant="outline">layer:{hit.layer}</Badge>
            {hit.recall_path && (
              <Badge variant="secondary" className="font-normal">
                recall:{hit.recall_path}
              </Badge>
            )}
            <Badge variant="outline" className="font-mono text-[11px]" title={`scope: ${hit.scope}`}>
              scope:{hit.scope}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground" title={ref}>
              id:{hit.id}
            </span>
          </div>
          <p className="text-sm">{hit.summary || t("hit.noSummary")}</p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          title={t("hit.viewProvenance")}
          onClick={() => navigate("provenance", { itemId: ref })}
        >
          <GitGraph className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {hit.provenance_summary && <span>{hit.provenance_summary}</span>}
        <span>· stage_conf {hit.stage_confidence.toFixed(2)}</span>
        {hit.tags.map((t) => (
          <Badge key={t} variant="secondary" className="font-normal">
            {t}
          </Badge>
        ))}
        <span className="font-mono break-all">· ref {ref}</span>
      </div>

      {full != null ? (
        <JsonView value={full} />
      ) : (
        <AsyncButton variant="outline" size="sm" loading={loading} onClick={expand}>
          {t("hit.expand")}
        </AsyncButton>
      )}
      {error ? <p className="text-xs text-destructive">{errorMessage(error)}</p> : null}
    </Card>
  );
}
