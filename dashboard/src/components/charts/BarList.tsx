import { cn } from "@/lib/utils";

export interface BarItem {
  label: string;
  value: number;
  /** Optional CSS background for the fill (gradient or solid). */
  color?: string;
}

/**
 * Horizontal bars normalized to the max value, with a trailing value column.
 * Ported from the static mock's `barChart()`.
 */
export function BarList({ items, className }: { items: BarItem[]; className?: string }) {
  const max = items.reduce((m, i) => Math.max(m, i.value), 1);

  return (
    <div className={cn("grid gap-2", className)}>
      {items.map((item) => {
        const pct = Math.round((item.value / max) * 100);
        return (
          <div
            key={item.label}
            className="grid grid-cols-[80px_1fr_42px] items-center gap-2 text-xs"
          >
            <span className="truncate text-muted-foreground">{item.label}</span>
            <span className="h-2.5 overflow-hidden rounded-full bg-muted">
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  background: item.color ?? "var(--primary)",
                }}
              />
            </span>
            <span className="text-right tabular-nums text-foreground">{item.value}</span>
          </div>
        );
      })}
    </div>
  );
}
