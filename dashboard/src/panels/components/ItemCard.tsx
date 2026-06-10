import { ChevronDown, ChevronRight, GitGraph } from "lucide-react";
import { useState } from "react";

import { JsonView } from "@/components/common/JsonView";
import { StabilityBadge, StageBadge } from "@/components/common/StageBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useNav } from "@/context/NavContext";
import { useI18n } from "@/lib/i18n";
import type { ContextItem } from "@/lib/types";

export function ItemCard({
  item,
  actions,
  defaultOpen = false,
}: {
  item: ContextItem;
  /** Optional lifecycle action buttons rendered in the footer. */
  actions?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const { navigate } = useNav();
  const deleted = Boolean(item.deleted_at);

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start justify-between gap-2 p-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          {open ? (
            <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <StageBadge stage={item.stage} />
              <StabilityBadge stability={item.stability} />
              {deleted && <Badge variant="destructive">deleted</Badge>}
              <span className="font-mono text-xs text-muted-foreground">{item.id}</span>
            </div>
            <div className="mt-1 line-clamp-2 text-sm">
              {item.summary || item.abstract || previewContent(item.content)}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>importance {item.importance.toFixed(2)}</span>
              <span>· access {item.access_count}</span>
              <span>· {formatTime(item.created_at)}</span>
              {item.tags.map((t) => (
                <Badge key={t} variant="secondary" className="font-normal">
                  {t}
                </Badge>
              ))}
            </div>
          </div>
        </button>
        <Button
          variant="ghost"
          size="icon"
          title={t("item.viewProvenance")}
          onClick={() => navigate("provenance", { itemId: item.id })}
        >
          <GitGraph className="h-4 w-4" />
        </Button>
      </div>

      {open && (
        <div className="space-y-3 border-t bg-muted/30 p-3">
          <Field label="content">
            <JsonView value={item.content} />
          </Field>
          {item.summary && (
            <Field label="summary">
              <p className="text-sm">{item.summary}</p>
            </Field>
          )}
          <Field label="provenance">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>source: {item.provenance.source_type}</span>
              <span>id: {item.provenance.source_id}</span>
              <span>confidence: {item.provenance.confidence.toFixed(2)}</span>
              <span>verified: {String(item.provenance.verified)}</span>
              {item.effective_confidence != null && (
                <span>effective: {item.effective_confidence.toFixed(2)}</span>
              )}
            </div>
          </Field>
          {item.links && item.links.length > 0 && (
            <Field label={`links (${item.links.length})`}>
              <div className="flex flex-col gap-1">
                {item.links.map((l, i) => (
                  <div key={`${l.target_id}-${i}`} className="flex items-center gap-2 text-xs">
                    <Badge variant="outline">{l.relation}</Badge>
                    <span className="font-mono text-muted-foreground">{l.target_id}</span>
                    <span className="text-muted-foreground">strength {l.strength.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </Field>
          )}
          {actions && <div className="flex flex-wrap gap-2 pt-1">{actions}</div>}
        </div>
      )}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}

function previewContent(content: unknown): string {
  if (typeof content === "string") return content;
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
