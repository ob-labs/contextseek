import { cn } from "@/lib/utils";

export interface StatCard {
  label: string;
  value: string | number;
}

/**
 * Compact KPI card grid. Ported from the static mock's `kpi()`.
 */
export function StatCards({
  cards,
  columns = 3,
  className,
}: {
  cards: StatCard[];
  columns?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("grid gap-2", className)}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {cards.map((c) => (
        <div key={c.label} className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground">
          {c.label}
          <strong className="mt-1 block text-lg font-semibold tabular-nums text-foreground">
            {c.value}
          </strong>
        </div>
      ))}
    </div>
  );
}
