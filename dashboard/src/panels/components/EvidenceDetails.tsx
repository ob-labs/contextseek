import { ConfidenceBar } from "@/components/common/ConfidenceBar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { EvidenceChain } from "@/lib/types";

export function EvidenceDetails({ chain }: { chain: EvidenceChain }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3 pt-6">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <Metric label="overall confidence">
              <ConfidenceBar value={chain.overall_confidence} />
            </Metric>
            <Metric label="critical path confidence">
              <ConfidenceBar value={chain.critical_path_confidence} />
            </Metric>
            <Metric label="max depth">
              <span className="font-mono text-sm">{chain.max_depth}</span>
            </Metric>
            <Metric label="total sources">
              <span className="font-mono text-sm">{chain.total_sources}</span>
            </Metric>
          </div>
          <div className="flex flex-wrap gap-2">
            {chain.has_conflicts && <Badge variant="destructive">存在冲突</Badge>}
            {chain.needs_reverification && <Badge variant="destructive">需重新验证</Badge>}
            {chain.broken_links.length > 0 && (
              <Badge variant="secondary">断链 {chain.broken_links.length}</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Section title={`关键路径 (${chain.critical_path.length})`}>
        {chain.critical_path.length ? (
          <div className="flex flex-wrap items-center gap-1 font-mono text-xs">
            {chain.critical_path.map((id, i) => (
              <span key={id} className="flex items-center gap-1">
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-900">{id}</span>
                {i < chain.critical_path.length - 1 && <span className="text-muted-foreground">→</span>}
              </span>
            ))}
          </div>
        ) : (
          <Empty />
        )}
      </Section>

      <Section title={`冲突 (${chain.conflicts.length})`}>
        {chain.conflicts.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-4">item</th>
                  <th className="py-1 pr-4">refuter</th>
                  <th className="py-1 pr-4">refutation</th>
                  <th className="py-1">net impact</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {chain.conflicts.map((c, i) => (
                  <tr key={`${c.item_id}-${i}`} className="border-t">
                    <td className="py-1 pr-4">{c.item_id}</td>
                    <td className="py-1 pr-4">{c.refuter_id}</td>
                    <td className="py-1 pr-4">{c.refutation_strength.toFixed(2)}</td>
                    <td className="py-1">{c.net_confidence_impact.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty />
        )}
      </Section>

      {chain.broken_links.length > 0 && (
        <Section title={`断链 (${chain.broken_links.length})`}>
          <div className="flex flex-wrap gap-1 font-mono text-xs">
            {chain.broken_links.map((id) => (
              <span key={id} className="rounded bg-rose-100 px-1.5 py-0.5 text-rose-900">
                {id}
              </span>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 text-sm font-medium">{title}</div>
      {children}
    </div>
  );
}

function Empty() {
  return <p className="text-xs text-muted-foreground">无</p>;
}
